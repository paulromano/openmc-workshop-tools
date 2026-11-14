"""Check generated configuration without installing the course software stack."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'nea_environment.py'
spec = importlib.util.spec_from_file_location('nea_environment', SOURCE)
nea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nea)


class EnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        home, root = base / 'home', base / 'nea'
        home.mkdir()
        xs = root / 'data/endfb-vii.1-hdf5/cross_sections.xml'
        xs.parent.mkdir(parents=True)
        xs.write_text('<cross_sections/>')
        self.args = argparse.Namespace(
            home=home, root=root, course=home / 'openmc-nea-course',
            cross_sections=xs, chain=root / 'data/depletion_chains/chain_endfb71_pwr.xml',
            unit_file=base / 'nea-jupyter.service',
            user=pwd.getpwuid(os.getuid()).pw_name, port=8888, allow_origin='*')
        self.args.course.mkdir()

    def configure(self, password_hash=None):
        with patch.dict(os.environ, {}, clear=True):
            if password_hash:
                os.environ['JUPYTER_PASSWORD_HASH'] = password_hash
            nea.configure(self.args)

    def test_shared_environment_and_legacy_paths(self):
        self.configure()
        env_file = self.args.home / '.config/openmc-nea/environment'
        result = subprocess.check_output(
            ['bash', '--noprofile', '--norc', '-c', 'set -a; . "$1"; env -0', 'bash', str(env_file)])
        env = dict(item.decode().split('=', 1) for item in result.split(b'\0') if item)
        self.assertEqual(env['PATH'].split(':')[0], str(self.args.root / 'venv/bin'))
        self.assertEqual(env['OPENMC_CROSS_SECTIONS'], str(self.args.cross_sections))
        self.assertEqual((self.args.home / 'data').resolve(), self.args.root / 'data')
        self.assertEqual((self.args.home / 'data/endfb71_hdf5').resolve(), self.args.cross_sections.parent)
        self.assertIn('nea-jupyter.service', (self.args.home / 'run_jupyter.sh').read_text())

    def test_password_preservation_and_repeated_configuration(self):
        self.configure('argon2:example-one')
        self.assertTrue(nea.password_configured(self.args.home))
        self.configure()
        self.configure()
        config = self.args.home / '.jupyter/jupyter_server_config.json'
        self.assertEqual(json.loads(config.read_text())['PasswordIdentityProvider']['hashed_password'],
                         'argon2:example-one')
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        for filename in ['.profile', '.bashrc']:
            self.assertEqual((self.args.home / filename).read_text().count(
                '# OpenMC NEA course environment'), 1)

    def test_legacy_password_and_existing_data_are_preserved(self):
        data = self.args.home / 'data'
        data.mkdir()
        (data / 'keep').write_text('course data')
        password = self.args.home / '.jupyter/jupyter_server_config.json'
        password.parent.mkdir()
        password.write_text(json.dumps({'IdentityProvider': {'hashed_password': 'sha1:example'}}))
        self.configure()
        self.assertTrue(nea.password_configured(self.args.home))
        self.assertFalse(data.is_symlink())
        self.assertEqual((data / 'keep').read_text(), 'course data')
        if importlib.util.find_spec('jupyter_server'):
            from jupyter_server.auth.identity import PasswordIdentityProvider

            identity = PasswordIdentityProvider(config=nea.jupyter_config(self.args.home))
            self.assertEqual(identity.hashed_password, 'sha1:example')

    def test_shell_metacharacters_are_rejected(self):
        self.args.root = self.args.root.with_name('nea;echo-injected')
        with self.assertRaises(ValueError):
            self.configure()

    @unittest.skipUnless(importlib.util.find_spec('jupyter_server'), 'Jupyter Server is unavailable')
    def test_jupyter_reads_the_generated_configuration(self):
        from jupyter_server.auth import passwd, passwd_check
        from jupyter_server.auth.identity import PasswordIdentityProvider
        from jupyter_server.serverapp import ServerApp

        hashed = passwd('temporary-test-password')
        self.configure(hashed)
        config = nea.jupyter_config(self.args.home)
        app = ServerApp(config=config)
        identity = PasswordIdentityProvider(config=config)
        self.assertEqual(app.port, 8888)
        self.assertEqual(app.root_dir, str(self.args.course))
        self.assertTrue(app.trust_xheaders)
        self.assertTrue(identity.password_required)
        self.assertTrue(passwd_check(identity.hashed_password, 'temporary-test-password'))

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze is unavailable')
    def test_systemd_accepts_the_generated_service(self):
        self.configure()
        executable = self.args.root / 'venv/bin/jupyter'
        nea.write_file(executable, '#!/bin/sh\nexit 0\n', 0o755)
        run = subprocess.run(['systemd-analyze', 'verify', str(self.args.unit_file)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_service_process_creates_a_readable_private_log(self):
        self.configure()
        executable = self.args.root / 'venv/bin/jupyter'
        nea.write_file(executable, '#!/bin/sh\necho "Jupyter output"\necho "Jupyter error" >&2\n', 0o755)
        start = next(line.removeprefix('ExecStart=') for line in
                     self.args.unit_file.read_text().splitlines() if line.startswith('ExecStart='))
        subprocess.run(shlex.split(start), check=True, umask=0o077)
        log = self.args.home / 'notebook_launch.log'
        self.assertEqual(log.stat().st_uid, os.getuid())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertEqual(log.read_text(), 'Jupyter output\nJupyter error\n')
        subprocess.run(shlex.split(start), check=True, umask=0o077)
        self.assertEqual(log.read_text().count('Jupyter output'), 2)


if __name__ == '__main__':
    unittest.main()
