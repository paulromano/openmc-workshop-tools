"""Manage course EC2 instances in a terminal dashboard."""

import argparse
import asyncio
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Select, Static

from dashboard_aws import EC2Service, LaunchRequest, REGION, launch_group
from launch_instances import IMAGE_ID, INSTANCE_TYPE


class ConfirmInstance(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancel')]

    def __init__(self, action, instance):
        super().__init__()
        self.operation = action
        self.instance = instance

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes='dialog'):
            yield Static(f'{self.operation.capitalize()} {self.instance.hostname}?',
                         classes='dialog-title', markup=False)
            yield Static(f'Group: {self.instance.group}\nInstance: {self.instance.instance_id}',
                         markup=False)
            if self.operation == 'reboot':
                yield Static('Rebooting interrupts the session and clears kernel memory. '
                             'Saved files remain on disk.')
            else:
                yield Static('Starting this instance resumes EC2 compute charges.')
            with Horizontal(classes='dialog-buttons'):
                yield Button('Cancel', id='cancel')
                yield Button(self.operation.capitalize(), id='confirm', variant='warning')

    def on_mount(self):
        self.query_one('#cancel', Button).focus()

    @on(Button.Pressed, '#confirm')
    def confirm(self):
        self.dismiss(True)

    @on(Button.Pressed, '#cancel')
    def action_cancel(self):
        self.dismiss(False)


class LaunchGroup(ModalScreen[LaunchRequest | None]):
    BINDINGS = [('escape', 'cancel', 'Cancel')]

    def compose(self) -> ComposeResult:
        with Vertical(classes='dialog launch-dialog'):
            yield Static('Launch a group', classes='dialog-title')
            with VerticalScroll(id='launch-fields'):
                yield Static(f'{REGION}  |  {INSTANCE_TYPE}\nAMI: {IMAGE_ID}', markup=False)
                with Horizontal(id='launch-inputs'):
                    with Vertical(id='launch-group-field'):
                        yield Label('Group name')
                        yield Input(placeholder='student', id='launch-name')
                    with Vertical(id='launch-count-field'):
                        yield Label('New instances')
                        yield Input('1', type='integer', id='launch-count')
                yield Checkbox('Skip NGINX proxy update', id='skip-proxy')
                yield Static('The launcher assigns hostnames and updates the proxy. '
                             'Existing groups gain instances. EC2 charges apply.')
            yield Static('', id='launch-error', markup=False)
            with Horizontal(classes='dialog-buttons'):
                yield Button('Cancel', id='cancel')
                yield Button('Launch', id='launch-confirm', variant='primary')

    def on_mount(self):
        self.query_one('#launch-name', Input).focus()

    @on(Button.Pressed, '#launch-confirm')
    def submit(self):
        try:
            request = LaunchRequest(
                self.query_one('#launch-name', Input).value.strip(),
                int(self.query_one('#launch-count', Input).value),
                self.query_one('#skip-proxy', Checkbox).value,
            )
            request.validate()
        except ValueError as exc:
            self.query_one('#launch-error', Static).update(str(exc))
            return
        self.dismiss(request)

    @on(Button.Pressed, '#cancel')
    def action_cancel(self):
        self.dismiss(None)


class Dashboard(App):
    TITLE = 'OpenMC course · EC2'
    BINDINGS = [
        ('g', 'groups', 'Groups'),
        ('r', 'refresh_instances', 'Refresh'),
        ('b', 'reboot', 'Reboot'),
        ('s', 'start_instance', 'Start'),
        ('n', 'launch', 'Launch group'),
        ('l', 'toggle_activity', 'Log'),
        ('q', 'quit', 'Quit'),
    ]
    CSS = '''
    Screen { background: $background; }
    #toolbar { height: 3; margin: 0 1; }
    #toolbar Label { padding: 1 1 0 0; }
    #groups { width: 1fr; max-width: 30; margin-right: 1; }
    #toolbar Button { min-width: 9; width: auto; margin-left: 1; }
    #summary { height: 1; margin: 0 2; }
    #instances { height: 1fr; min-height: 4; margin: 0 1; border: solid $primary; }
    #details { height: auto; min-height: 3; max-height: 6; margin: 0 2; }
    #refresh-status { height: auto; max-height: 3; margin: 0 2; }
    #health-hint { height: 1; margin: 0 2; color: $text-muted; }
    #activity { display: none; height: 6; margin: 0 1; border: solid $primary; }
    ConfirmInstance, LaunchGroup { align: center middle; background: $background 70%; }
    .dialog {
        width: 68; max-width: 95%; height: auto; max-height: 95%;
        padding: 1 2; border: thick $accent; background: $surface;
    }
    .dialog-title { text-style: bold; margin-bottom: 1; }
    .dialog Label { margin-top: 1; }
    .launch-dialog { height: 26; padding: 0 2; }
    .launch-dialog Label { margin-top: 0; }
    #launch-fields { height: 1fr; }
    #launch-inputs { height: 4; }
    #launch-group-field { width: 2fr; margin-right: 1; }
    #launch-count-field { width: 1fr; }
    .dialog-buttons { height: 3; margin-top: 1; align-horizontal: right; }
    .dialog-buttons Button { margin-left: 1; }
    #launch-error { color: $error; height: auto; max-height: 4; }
    '''

    def __init__(self, group=None, refresh_interval=30, service=None):
        super().__init__()
        self.group = group
        self.refresh_interval = refresh_interval
        self.service = service if service is not None else EC2Service()
        self.snapshot = None
        self.updated_at = None
        self.refresh_error = ''
        self.refreshing = False
        self.operation = ''
        self.remembered_groups = {group} if group else set()
        self.group_options = []
        self.shown_instances = ()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id='toolbar'):
            yield Label('Group')
            yield Select([], prompt='Select a group', id='groups')
            yield Button('Refresh', id='refresh')
            yield Button('Reboot', id='reboot', disabled=True)
            yield Button('Start', id='start', disabled=True)
            yield Button('Launch group', id='launch', variant='primary')
        yield Static('Loading course instances…', id='summary', markup=False)
        yield DataTable(id='instances', cursor_type='row', zebra_stripes=True)
        yield Static('Select an instance with the arrow keys.', id='details', markup=False)
        yield Static('', id='refresh-status', markup=False)
        yield Static('EC2 checks cover host and OS health; Jupyter can hang while checks pass.',
                     id='health-hint')
        yield RichLog(id='activity', max_lines=200, wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self):
        # Keep references to the base screen's widgets while dialogs are open.
        self.table = self.query_one('#instances', DataTable)
        self.groups = self.query_one('#groups', Select)
        self.summary = self.query_one('#summary', Static)
        self.details = self.query_one('#details', Static)
        self.refresh_status = self.query_one('#refresh-status', Static)
        self.activity = self.query_one('#activity', RichLog)
        self.reboot_button = self.query_one('#reboot', Button)
        self.start_button = self.query_one('#start', Button)
        self.launch_button = self.query_one('#launch', Button)
        self.activity.border_title = 'Activity'
        self.table.add_columns('Hostname', 'State', 'System check', 'Instance check',
                               'Private IP', 'Instance ID', 'Type', 'Public DNS')
        self.table.focus()
        self.action_refresh_instances()
        self.set_interval(self.refresh_interval, self.action_refresh_instances)

    def log_message(self, message):
        self.activity.display = True
        self.activity.write(Text(f'{datetime.now():%H:%M:%S}  {message}'))

    def action_toggle_activity(self):
        self.activity.display = not self.activity.display

    def action_refresh_instances(self):
        if not self.refreshing:
            self.refreshing = True
            self.update_refresh_status()
            self.fetch_instances()

    @work(group='refresh')
    async def fetch_instances(self):
        try:
            self.snapshot = await asyncio.to_thread(self.service.snapshot)
            self.updated_at = datetime.now()
            self.refresh_error = ''
            self.update_groups()
            if self.snapshot.health_error:
                self.log_message(f'Health checks unavailable: {self.snapshot.health_error}')
        except Exception as exc:
            self.refresh_error = str(exc)
            self.log_message(f'Refresh failed: {exc}')
            if self.snapshot is None:
                self.summary.update('Course instances unavailable. Check Activity and press r to retry.')
        finally:
            self.refreshing = False
            self.update_refresh_status()
            self.update_selection()

    def update_groups(self):
        groups = sorted({instance.group for instance in self.snapshot.instances}
                        | self.remembered_groups)
        if self.group not in groups:
            self.group = 'student' if 'student' in groups else next(iter(groups), None)
        if groups != self.group_options:
            self.group_options = groups
            self.groups.set_options([(Text(group or '(empty group tag)'), group) for group in groups])
        self.groups.prompt = 'Select a group' if groups else 'No groups found'
        self.groups.value = self.group if self.group is not None else Select.NULL
        self.render_instances()

    @on(Select.Changed, '#groups')
    def group_changed(self, event):
        if event.value != self.groups.value:
            return
        if event.value is Select.NULL and self.group is not None:
            self.groups.value = self.group
        elif isinstance(event.value, str):
            self.group = event.value
            self.render_instances()

    @staticmethod
    def status_text(value):
        style = {'running': 'green', 'ok': 'green', 'impaired': 'bold red',
                 'pending': 'yellow', 'stopping': 'yellow', 'initializing': 'yellow',
                 'unknown': 'yellow', 'insufficient-data': 'yellow'}.get(value, '')
        return Text(value, style=style)

    @property
    def selected_instance(self):
        row = self.table.cursor_row
        return self.shown_instances[row] if 0 <= row < len(self.shown_instances) else None

    def render_instances(self):
        selected = self.selected_instance
        instances = tuple(instance for instance in self.snapshot.instances if instance.group == self.group)
        if instances != self.shown_instances:
            self.shown_instances = instances
            self.table.clear()
            for instance in instances:
                self.table.add_row(Text(instance.hostname), self.status_text(instance.state),
                                   self.status_text(instance.system_check),
                                   self.status_text(instance.instance_check),
                                   Text(instance.private_ip), Text(instance.instance_id),
                                   Text(instance.instance_type), Text(instance.public_dns),
                                   key=instance.instance_id)
            row = next((index for index, item in enumerate(instances)
                        if selected and item.instance_id == selected.instance_id), 0)
            self.table.move_cursor(row=row)
        running = sum(item.state == 'running' for item in instances)
        impaired = sum('impaired' in (item.system_check, item.instance_check) for item in instances)
        self.summary.update(
            f'{self.group if self.group is not None else "No group"}  ·  {len(instances)} instances  ·  '
            f'{running} running  ·  {impaired} with failed checks  ·  {REGION}')
        self.update_selection()

    @on(DataTable.RowHighlighted, '#instances')
    def row_highlighted(self):
        self.update_selection()

    def update_selection(self):
        instance = self.selected_instance
        if instance:
            self.details.update(
                f'{instance.hostname}  |  {instance.instance_id}  |  {instance.instance_type}\n'
                f'Private IP: {instance.private_ip}  |  State: {instance.state}\n'
                f'Public DNS: {instance.public_dns}')
        else:
            self.details.update('No instances in this group. Use Launch group to create instances.'
                                if self.snapshot is not None else 'Waiting for course instances.')
        ready = instance is not None and not self.operation and not self.refresh_error
        self.reboot_button.disabled = not (ready and instance.state == 'running')
        self.start_button.disabled = not (ready and instance.state == 'stopped')
        self.launch_button.disabled = bool(self.operation)

    def update_refresh_status(self):
        when = f'Last updated {self.updated_at:%H:%M:%S}' if self.updated_at else 'Waiting for EC2'
        if self.refresh_error:
            text = f'STALE · {when} · Refresh failed: {self.refresh_error}'
        elif self.snapshot and self.snapshot.health_error:
            text = f'{when} · Health checks unavailable: {self.snapshot.health_error}'
        else:
            text = f'{when} · Every {self.refresh_interval:g}s'
        if self.refreshing:
            text += ' · Refreshing…'
        if self.operation:
            text += f' · {self.operation}'
        self.refresh_status.update(text)

    def action_groups(self):
        if len(self.screen_stack) == 1:
            self.groups.focus()
            self.groups.action_show_overlay()

    @on(Button.Pressed, '#refresh')
    def refresh_pressed(self):
        self.action_refresh_instances()

    @on(Button.Pressed, '#reboot')
    def action_reboot(self):
        self.confirm_instance_action('reboot', 'running')

    @on(Button.Pressed, '#start')
    def action_start_instance(self):
        self.confirm_instance_action('start', 'stopped')

    def confirm_instance_action(self, action, state):
        if len(self.screen_stack) != 1 or self.operation:
            return
        instance = self.selected_instance
        if instance is None or instance.state != state or self.refresh_error:
            self.notify(f'Select a {state} instance in a refreshed table.', severity='warning')
            return

        def confirmed(accepted):
            if accepted:
                self.operation = f'{action.capitalize()} request for {instance.hostname}'
                self.update_selection()
                self.update_refresh_status()
                self.change_instance(action, instance)

        self.push_screen(ConfirmInstance(action, instance), confirmed)

    @work(group='operation')
    async def change_instance(self, action, instance):
        try:
            await asyncio.to_thread(self.service.change_state, action, instance)
            self.log_message(f'{action.capitalize()} requested for {instance.hostname} ({instance.instance_id}).')
            if action == 'reboot':
                self.log_message('EC2 may still show running during a reboot; allow a few minutes for recovery.')
        except Exception as exc:
            self.log_message(f'{action.capitalize()} failed: {exc}')
            self.notify(f'{action.capitalize()} failed. See Activity.', severity='error')
        finally:
            self.operation = ''
            self.update_selection()
            self.update_refresh_status()
            self.action_refresh_instances()

    @on(Button.Pressed, '#launch')
    def action_launch(self):
        if len(self.screen_stack) != 1 or self.operation:
            return

        def confirmed(request):
            if request is not None:
                self.operation = f'Launching {request.count} instances in {request.group}'
                self.remembered_groups.add(request.group)
                self.group = request.group
                if self.snapshot is not None:
                    self.update_groups()
                self.update_selection()
                self.update_refresh_status()
                self.launch_instances(request)

        self.push_screen(LaunchGroup(), confirmed)

    @work(group='operation')
    async def launch_instances(self, request):
        self.log_message(self.operation)
        try:
            code = await launch_group(request, self.log_message)
            if code:
                self.log_message(f'Launcher exited with status {code}. Some instances may already exist. '
                                 'Check Activity and the table before launching again. If only the proxy '
                                 f'update failed, retry: python scripts/update_proxy.py {request.group}')
                self.notify('Launch failed. See Activity before retrying.', severity='error')
            else:
                self.log_message(f'Launch complete for {request.group}. Jupyter may still be starting.')
        except Exception as exc:
            self.log_message(f'Launch failed: {exc}. Check the table before retrying.')
            self.notify('Launch failed. See Activity.', severity='error')
        finally:
            self.operation = ''
            self.update_selection()
            self.update_refresh_status()
            self.action_refresh_instances()

    async def action_quit(self):
        if self.operation:
            self.notify('Wait for the current operation to finish before quitting.', severity='warning')
        else:
            self.exit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', help='Group to select initially (ws_group tag)')
    parser.add_argument('--refresh-interval', type=float, default=30, metavar='SECONDS',
                        help='Seconds between refreshes, at least 5 (default: 30)')
    args = parser.parse_args(argv)
    if not 5 <= args.refresh_interval < float('inf'):
        parser.error('--refresh-interval must be a finite number of at least 5 seconds')
    Dashboard(group=args.group, refresh_interval=args.refresh_interval).run()


if __name__ == '__main__':
    main()
