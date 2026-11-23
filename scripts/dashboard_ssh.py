"""Embed an interactive OpenSSH client in the dashboard."""

import asyncio
from pathlib import Path
import subprocess

from rich.segment import Segment
from rich.style import Style
from textual import events, on
from textual.message import Message
from textual.strip import Strip
from textual_tty import Terminal

from update_proxy import KEYPAIR_PATH


SSH_USER = 'ubuntu'
SSH_KEY = KEYPAIR_PATH


def ssh_command(host, user=SSH_USER, key=SSH_KEY):
    """Build arguments without a shell, retaining the user's SSH configuration."""
    command = ['ssh', '-A', '-t', '-o', 'ConnectTimeout=15',
               '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-l', user]
    if key:
        command.extend(['-i', str(Path(key).expanduser())])
    command.extend(['--', host])
    return command


class _SSHChrome(Terminal.CHROME):
    def on_clipboard(self, selection, text):
        # Remote escape sequences stay inside the terminal emulator.
        pass

    def on_notify(self, text):
        pass


class SSHSession(Terminal):
    """A terminal widget that reports exits and reaps its SSH client on close."""

    CHROME = _SSHChrome

    class Exited(Message):
        def __init__(self, session, code):
            super().__init__()
            self.session = session
            self.code = code

        @property
        def control(self):
            return self.session

    @property
    def process(self):
        return self._process

    async def on_mount(self, event: events.Mount):
        # Terminal initializes Monitor itself. Avoid Textual dispatching those
        # parent handlers again, which would spawn a second SSH client.
        event.prevent_default()
        await super().on_mount()
        if self.process is None:
            self.feed('Unable to start the SSH client.\r\n')
            self.post_message(self.Exited(self, 127))

    @on(Terminal.ProcessExited)
    def process_exited(self, event):
        event.stop()
        self.post_message(self.Exited(self, event.exit_code))

    def render_line(self, y):
        strip = super().render_line(y)
        # Textual's no-color filter requires styles even on padding added
        # while the widget and the child terminal are being resized.
        return Strip([Segment(segment.text, segment.style or Style(), segment.control)
                      for segment in strip], strip.cell_length)

    async def on_unmount(self):
        process = self.process
        self.board.stop_process()
        if process is not None:
            try:
                await asyncio.to_thread(process.wait, timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
