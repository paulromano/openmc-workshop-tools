#!/usr/bin/env python3
"""Configure the NEA notebook environment and check a completed installation."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def write_file(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def configure(args):
    home, root, course = args.home, args.root, args.course
    software, venv = root / 'software', root / 'venv'
    bins = [venv / 'bin', *(software / p / 'bin' for p in
                           ['openmc', 'dagmc', 'moab', 'njoy2016', 'libmesh'])]
    env = {
        'PATH': ':'.join(map(str, bins)) + ':/usr/local/bin:/usr/bin:/bin',
        'LD_LIBRARY_PATH': ':'.join(str(software / p / 'lib') for p in
                                   ['openmc', 'dagmc', 'moab', 'double-down', 'libmesh']),
        'PKG_CONFIG_PATH': str(software / 'libmesh/lib/pkgconfig'),
        'OPENMC_CROSS_SECTIONS': str(args.cross_sections.resolve()),
        'OPENMC_CHAIN_FILE': str(args.chain.resolve()),
        'VIRTUAL_ENV': str(venv),
        'PYVISTA_OFF_SCREEN': 'true',
    }
    # This file is read by both systemd and a POSIX shell.
    if any(not re.fullmatch(r'[-A-Za-z0-9_./:+]+', value) for value in env.values()):
        raise ValueError('Environment paths must use letters, numbers, /, ., _, -, +, or :')
    env_path = home / '.config/openmc-nea/environment'
    write_file(env_path, ''.join(f'{k}={v}\n' for k, v in env.items()))
    # Preserve paths used directly by older course notebooks.
    for name, destination in [('data', root / 'data'), ('software', software)]:
        link = home / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(destination, target_is_directory=True)
    legacy_xs = root / 'data/endfb71_hdf5'
    if not legacy_xs.exists() and not legacy_xs.is_symlink():
        legacy_xs.symlink_to(args.cross_sections.resolve().parent, target_is_directory=True)
    marker = '# OpenMC NEA course environment'
    hook = (f'\n{marker}\n'
            'if [ -r "$HOME/.config/openmc-nea/environment" ]; then\n'
            '    set -a\n'
            '    . "$HOME/.config/openmc-nea/environment"\n'
            '    set +a\n'
            'fi\n')
    for name in ['.profile', '.bashrc']:
        p = home / name
        previous = p.read_text() if p.exists() else ''
        if marker not in previous:
            mode = p.stat().st_mode & 0o777 if p.exists() else 0o644
            write_file(p, previous + hook, mode)

    jupyter = home / '.jupyter'
    jupyter.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = jupyter / 'jupyter_server_config.py'
    if config.exists() and not (jupyter / 'jupyter_server_config.py.pre-nea').exists():
        shutil.copy2(config, jupyter / 'jupyter_server_config.py.pre-nea')
    settings = {
        'ServerApp.ip': '0.0.0.0',
        'ServerApp.port': args.port,
        'ServerApp.port_retries': 0,
        'ServerApp.open_browser': False,
        'ServerApp.allow_remote_access': True,
        'ServerApp.allow_origin': args.allow_origin,
        'ServerApp.trust_xheaders': True,
        'ServerApp.root_dir': str(course),
        'ServerApp.default_url': '/lab',
        'PasswordIdentityProvider.password_required': True,
    }
    write_file(config, '# NEA course server, behind the course NGINX proxy.\n'
               'c = get_config()\n' + ''.join(f'c.{k} = {v!r}\n' for k, v in settings.items()), 0o600)
    password_hash = os.environ.get('JUPYTER_PASSWORD_HASH')
    if password_hash:
        p = jupyter / 'jupyter_server_config.json'
        stored = json.loads(p.read_text()) if p.exists() else {}
        stored.setdefault('PasswordIdentityProvider', {})['hashed_password'] = password_hash
        stored.get('IdentityProvider', {}).pop('hashed_password', None)
        write_file(p, json.dumps(stored, indent=2) + '\n', 0o600)

    write_file(home / 'run_jupyter.sh', '#!/usr/bin/env bash\nset -euo pipefail\n'
               'sudo systemctl start nea-jupyter.service\n', 0o755)
    write_file(args.unit_file, f'''[Unit]
Description=OpenMC NEA JupyterLab
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={args.user}
WorkingDirectory={course}
Environment=HOME={home}
Environment=JUPYTER_CONFIG_DIR={jupyter}
EnvironmentFile={env_path}
ExecStart={venv}/bin/jupyter lab
Restart=on-failure
RestartSec=5
UMask=0077
StandardOutput=append:{home}/notebook_launch.log
StandardError=inherit

[Install]
WantedBy=multi-user.target
''')


def password_configured(home):
    path = home / '.jupyter/jupyter_server_config.json'
    if not path.exists():
        return False
    config = json.loads(path.read_text())
    return any(config.get(section, {}).get(key) for section, key in [
        ('PasswordIdentityProvider', 'hashed_password'),
        ('IdentityProvider', 'hashed_password'),
        ('ServerApp', 'password'),
    ])


def jupyter_config(home):
    """Read Jupyter's two config files without starting or binding a server."""
    from traitlets.config.loader import JSONFileConfigLoader, PyFileConfigLoader

    directory = home / '.jupyter'
    config = PyFileConfigLoader('jupyter_server_config.py', path=str(directory)).load_config()
    if (directory / 'jupyter_server_config.json').exists():
        config.merge(JSONFileConfigLoader('jupyter_server_config.json', path=str(directory)).load_config())
    return config


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def verify(root, transport=False):
    # Use a headless backend only in this check, leaving notebook backends alone.
    os.environ.setdefault('MPLBACKEND', 'Agg')
    import numpy as np
    import openmc
    import openmc.deplete
    import openmc.lib
    import pyvista as pv

    for name in ['jupyterlab', 'notebook', 'ipykernel', 'ipywidgets', 'h5py',
                 'scipy', 'pandas', 'matplotlib', 'vtk', 'mcpl', 'endf', 'lxml',
                 'openmc_mcnp_adapter']:
        importlib.import_module(name)
    executable = root / 'software/openmc/bin/openmc'
    if Path(shutil.which('openmc') or '') != executable:
        raise RuntimeError('PATH selects a different OpenMC executable')
    version = subprocess.check_output([str(executable), '--version'], text=True)
    for feature in ['DAGMC support', 'libMesh support', 'PNG support']:
        if not re.search(re.escape(feature) + r':\s+yes', version):
            raise RuntimeError(f'Missing OpenMC feature: {feature}')
    prefix_lib = root / 'software/openmc/lib/libopenmc.so'
    python_lib = Path(openmc.lib._dll._name)
    if digest(prefix_lib) != digest(python_lib):
        raise RuntimeError('The executable and Python package have different OpenMC libraries')
    xs = Path(os.environ['OPENMC_CROSS_SECTIONS'])
    xs_root = ET.parse(xs).getroot()
    data_base = xs_root.findtext('directory') or str(xs.parent)
    data_base = Path(data_base)
    if not data_base.is_absolute():
        data_base = xs.parent / data_base
    for library in xs_root.findall('library'):
        path = data_base / library.attrib['path']
        if not path.is_file():
            raise FileNotFoundError(f'Cross-section file missing: {path}')
    chain_path = Path(os.environ['OPENMC_CHAIN_FILE'])
    chain = openmc.deplete.Chain.from_xml(chain_path)
    if not chain.nuclides:
        raise RuntimeError('The depletion chain is empty')
    from jupyter_server.auth.identity import PasswordIdentityProvider
    from jupyter_server.serverapp import ServerApp

    config = jupyter_config(Path.home())
    ServerApp(config=config)
    identity = PasswordIdentityProvider(config=config)
    if not identity.password_required or not identity.hashed_password:
        raise RuntimeError('Jupyter must have password authentication configured')
    subprocess.run(['njoy'], input='stop\n', text=True, check=True,
                   stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='nea-check-') as tmp:
        tmp = Path(tmp)
        pv.Sphere().save(tmp / 'sphere.vtp')
        if pv.read(tmp / 'sphere.vtp').n_points == 0:
            raise RuntimeError('VTK/PyVista file check failed')
        plotter = pv.Plotter(off_screen=True, window_size=(64, 64))
        try:
            plotter.add_mesh(pv.Sphere())
            plotter.screenshot(tmp / 'sphere.png')
        finally:
            plotter.close()
        if not (tmp / 'sphere.png').is_file():
            raise RuntimeError('VTK/PyVista rendering check failed')
        if transport:
            material = openmc.Material(name='water')
            material.add_nuclide('H1', 2.0)
            material.add_nuclide('O16', 1.0)
            material.set_density('g/cm3', 1.0)
            boundary = openmc.Sphere(r=10.0, boundary_type='reflective')
            model = openmc.Model(geometry=openmc.Geometry([
                openmc.Cell(fill=material, region=-boundary)]))
            model.settings.run_mode = 'fixed source'
            model.settings.batches = 2
            model.settings.particles = 100
            model.settings.source = openmc.IndependentSource(
                space=openmc.stats.Point(), energy=openmc.stats.Discrete([1e6], [1.0]))
            tally = openmc.Tally(name='flux')
            tally.scores = ['flux']
            model.tallies = [tally]
            statepoint = model.run(cwd=tmp, threads=1, output=False)
            with openmc.StatePoint(statepoint) as sp:
                if not np.all(sp.get_tally(name='flux').mean > 0):
                    raise RuntimeError('Executable transport check returned no flux')
            previous = Path.cwd()
            try:
                os.chdir(tmp)
                model.export_to_xml()
                with openmc.lib.run_in_memory(output=False):
                    openmc.lib.run()
            finally:
                os.chdir(previous)
    print(version)
    print(f'Python API: {openmc.__version__}; nuclear data, chain, NJOY, Jupyter, and VTK checked.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    config = commands.add_parser('configure')
    for name in ['home', 'root', 'course', 'cross-sections', 'chain', 'unit-file']:
        config.add_argument('--' + name, required=True, type=Path)
    config.add_argument('--user', required=True)
    config.add_argument('--port', type=int, default=8888)
    config.add_argument('--allow-origin', default='*')
    password = commands.add_parser('password-configured')
    password.add_argument('--home', required=True, type=Path)
    check = commands.add_parser('verify')
    check.add_argument('--root', required=True, type=Path)
    check.add_argument('--transport', action='store_true')
    args = parser.parse_args()
    if args.command == 'configure':
        if not 1 <= args.port <= 65535:
            parser.error('Port must be between 1 and 65535')
        configure(args)
    elif args.command == 'password-configured':
        raise SystemExit(0 if password_configured(args.home) else 1)
    else:
        verify(args.root, args.transport)


if __name__ == '__main__':
    main()
