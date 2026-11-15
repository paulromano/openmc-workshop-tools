"""Manage course EC2 instances in a terminal dashboard."""

import argparse
import asyncio
from datetime import datetime

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Select, Static

from dashboard_aws import ACTION_STATES, EC2Service, LaunchRequest, REGION, TERMINATABLE_STATES, launch_group
from dashboard_ssh import SSH_KEY, SSH_USER, run_ssh, ssh_command
from launch_instances import IMAGE_ID, INSTANCE_TYPE


class InstanceTable(DataTable):
    def on_click(self, event: events.Click):
        # DataTable normally selects only when clicking an already highlighted
        # cell. Use its cell metadata to toggle each row on the first click.
        row = event.style.meta.get('row', -1)
        column = event.style.meta.get('column', -1)
        if 0 <= row < self.row_count and column >= 0 and not event.style.meta.get('out_of_bounds'):
            self.move_cursor(row=row, column=column)
            self.post_message(DataTable.RowSelected(self, row, self.ordered_rows[row].key))
            event.prevent_default()
            event.stop()


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


class ConfirmSelection(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', 'Cancel')]

    def __init__(self, action, instances):
        super().__init__()
        self.operation = action
        self.instances = instances

    def compose(self) -> ComposeResult:
        with Vertical(classes='dialog selection-dialog'):
            yield Static(f'{self.operation.capitalize()} {len(self.instances)} selected instances?',
                         classes='dialog-title', markup=False)
            yield Static(f'Group: {self.instances[0].group}  |  {len(self.instances)} instances',
                         markup=False)
            if self.operation == 'start':
                yield Static('Starting the selected instances resumes EC2 compute charges. '
                             'Allow time for the notebooks to start.')
            elif self.operation == 'stop':
                yield Static('Stopping interrupts the selected sessions and clears kernel memory. '
                             'Files on EBS disks remain available when the instances start again.')
            elif self.operation == 'reboot':
                yield Static('Rebooting interrupts the selected sessions and clears kernel memory. '
                             'Saved files remain on disk.')
            else:
                yield Static('Termination is permanent. Data on disks configured for deletion, '
                             'including saved notebooks on those disks, will be lost.')
            with VerticalScroll(id='reviewed-instances'):
                yield Static('\n'.join(f'{instance.hostname}  {instance.instance_id}  ({instance.state})'
                                       for instance in self.instances), markup=False)
            with Horizontal(classes='dialog-buttons'):
                yield Button('Cancel', id='cancel')
                yield Button(self.operation.capitalize(), id='confirm-selection',
                             variant={'start': 'success', 'stop': 'warning',
                                      'reboot': 'warning', 'terminate': 'error'}[self.operation])

    def on_mount(self):
        self.query_one('#cancel', Button).focus()

    @on(Button.Pressed, '#confirm-selection')
    def confirm(self):
        self.dismiss(True)

    @on(Button.Pressed, '#cancel')
    def action_cancel(self):
        self.dismiss(False)


class Dashboard(App):
    TITLE = 'OpenMC course · EC2'
    BINDINGS = [
        ('g', 'groups', 'Groups'),
        ('r', 'refresh_instances', 'Refresh'),
        ('h', 'ssh', 'SSH'),
        ('b', 'reboot', 'Reboot'),
        ('s', 'start', 'Start'),
        ('S', 'stop', 'Stop'),
        ('n', 'launch', 'Launch'),
        ('t', 'terminate_selected', 'Terminate'),
        Binding('space', 'toggle_mark', 'Select row', show=False),
        Binding('a', 'select_all', 'Select all', show=False),
        Binding('c', 'clear_marks', 'Clear selection', show=False),
        Binding('l', 'toggle_activity', 'Log', show=False),
        ('q', 'quit', 'Quit'),
    ]
    CSS = '''
    Screen { background: $background; }
    #toolbar { height: 3; margin: 0 1; }
    #toolbar Label { padding: 1 1 0 0; }
    #groups { width: 1fr; max-width: 30; margin-right: 1; }
    #toolbar Button { min-width: 9; width: auto; margin-left: 1; }
    #selectionbar { height: 1; margin: 0 1; }
    #selectionbar Button { min-width: 6; width: auto; margin-right: 1; }
    #selection-count { width: 1fr; content-align: left middle; }
    #summary { height: 1; margin: 0 2; }
    #instances { height: 1fr; min-height: 4; margin: 0 1; border: solid $primary; }
    #details { height: auto; min-height: 3; max-height: 6; margin: 0 2; }
    #refresh-status { height: auto; max-height: 3; margin: 0 2; }
    #health-hint { height: 1; margin: 0 2; color: $text-muted; }
    #activity { display: none; height: 6; margin: 0 1; border: solid $primary; }
    LaunchGroup, ConfirmSelection {
        align: center middle; background: $background 70%;
    }
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
    .selection-dialog { height: 24; }
    #reviewed-instances { height: 1fr; min-height: 2; border: solid $primary; margin-top: 1; }
    '''

    def __init__(self, group=None, refresh_interval=30, service=None,
                 ssh_user=SSH_USER, ssh_key=SSH_KEY, ssh_private_ip=False):
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
        self.marked_ids = set()
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.ssh_private_ip = ssh_private_ip
        self.ssh_active = False
        self.ssh_refresh_pending = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id='toolbar'):
            yield Label('Group')
            yield Select([], prompt='Select a group', id='groups')
            yield Button('Refresh', id='refresh')
            yield Button('SSH', id='ssh', disabled=True)
            yield Button('Launch group', id='launch', variant='primary')
        with Horizontal(id='selectionbar'):
            yield Button('Select all', id='select-all', compact=True, disabled=True)
            yield Button('Clear', id='clear-marks', compact=True, disabled=True)
            yield Button('Start', id='start', variant='success', compact=True, disabled=True)
            yield Button('Stop', id='stop', variant='warning', compact=True, disabled=True)
            yield Button('Reboot', id='reboot', variant='warning', compact=True, disabled=True)
            yield Button('Terminate', id='terminate', variant='error', compact=True, disabled=True)
            yield Static('0 selected', id='selection-count', markup=False)
        yield Static('Loading course instances…', id='summary', markup=False)
        yield InstanceTable(id='instances', cursor_type='row', zebra_stripes=True)
        yield Static('Select an instance with the arrow keys.', id='details', markup=False)
        yield Static('', id='refresh-status', markup=False)
        yield Static('EC2 checks cover host and OS health; Jupyter can hang while checks pass.',
                     id='health-hint')
        yield RichLog(id='activity', max_lines=200, wrap=True, markup=False, highlight=False)
        yield Footer(show_command_palette=False, compact=True)

    def on_mount(self):
        # Keep references to the base screen's widgets while dialogs are open.
        self.table = self.query_one('#instances', DataTable)
        self.groups = self.query_one('#groups', Select)
        self.summary = self.query_one('#summary', Static)
        self.details = self.query_one('#details', Static)
        self.refresh_status = self.query_one('#refresh-status', Static)
        self.activity = self.query_one('#activity', RichLog)
        self.ssh_button = self.query_one('#ssh', Button)
        self.ssh_button.tooltip = 'SSH into the highlighted running instance (h)'
        self.reboot_button = self.query_one('#reboot', Button)
        self.start_button = self.query_one('#start', Button)
        self.stop_button = self.query_one('#stop', Button)
        self.action_buttons = {'start': self.start_button, 'stop': self.stop_button,
                               'reboot': self.reboot_button}
        self.launch_button = self.query_one('#launch', Button)
        self.select_all_button = self.query_one('#select-all', Button)
        self.clear_marks_button = self.query_one('#clear-marks', Button)
        self.terminate_button = self.query_one('#terminate', Button)
        self.action_buttons['terminate'] = self.terminate_button
        self.selection_count = self.query_one('#selection-count', Static)
        self.activity.border_title = 'Activity'
        self.table.border_subtitle = 'Space/Enter: select · a: all · c: clear'
        self.table.add_column('Select', key='marked', width=6)
        self.table.add_columns('Hostname', 'State', 'System check', 'Instance check',
                               'Private IP', 'Instance ID', 'Type', 'Public DNS')
        self.table.focus()
        self.action_refresh_instances()
        self.set_interval(self.refresh_interval, self.action_refresh_instances)

    def log_message(self, message, reveal=True):
        if reveal:
            self.activity.display = True
        self.activity.write(Text(f'{datetime.now():%H:%M:%S}  {message}'))

    def action_toggle_activity(self):
        self.activity.display = not self.activity.display

    def action_refresh_instances(self):
        if not self.refreshing and not self.ssh_active:
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
            if self.ssh_refresh_pending and not self.ssh_active:
                self.ssh_refresh_pending = False
                self.action_refresh_instances()

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
        # Keep selections across refreshes by ID, and limit them to the visible
        # group and instances that have not already begun shutting down.
        self.marked_ids.intersection_update(instance.instance_id for instance in instances
                                            if instance.state in TERMINATABLE_STATES)
        if instances != self.shown_instances:
            self.shown_instances = instances
            self.table.clear()
            for instance in instances:
                self.table.add_row(self.mark_text(instance), Text(instance.hostname),
                                   self.status_text(instance.state),
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
        self.launch_button.disabled = bool(self.operation)
        self.ssh_button.disabled = not (instance and instance.state == 'running'
                                        and not self.operation and not self.refresh_error)
        selected = self.marked_instances
        can_change = bool(selected) and not self.operation and not self.refresh_error
        for action, button in self.action_buttons.items():
            allowed = all(item.state in ACTION_STATES[action] for item in selected)
            button.disabled = not (can_change and allowed)
            if not selected:
                button.tooltip = f'Select instances to {action}'
            elif allowed:
                button.tooltip = f'{action.capitalize()} the selected instances'
            else:
                button.tooltip = self.state_requirement(action)
        eligible = any(item.state in TERMINATABLE_STATES for item in self.shown_instances)
        self.select_all_button.disabled = not eligible or bool(self.operation)
        self.clear_marks_button.disabled = not self.marked_ids or bool(self.operation)
        self.selection_count.update(f'{len(self.marked_ids)} selected')

    def mark_text(self, instance):
        if instance.state not in TERMINATABLE_STATES:
            return Text('—')
        return Text('[x]' if instance.instance_id in self.marked_ids else '[ ]')

    @property
    def marked_instances(self):
        return tuple(item for item in self.shown_instances if item.instance_id in self.marked_ids)

    def update_marks(self):
        for instance in self.shown_instances:
            self.table.update_cell(instance.instance_id, 'marked', self.mark_text(instance))
        self.update_selection()

    def toggle_mark(self, instance_id):
        if self.operation or len(self.screen_stack) != 1:
            return
        instance = next((item for item in self.shown_instances if item.instance_id == instance_id), None)
        if instance and instance.state in TERMINATABLE_STATES:
            if instance_id in self.marked_ids:
                self.marked_ids.remove(instance_id)
            else:
                self.marked_ids.add(instance_id)
            self.update_marks()

    def action_toggle_mark(self):
        if self.table.has_focus and self.selected_instance:
            self.toggle_mark(self.selected_instance.instance_id)

    @on(DataTable.RowSelected, '#instances')
    def row_selected(self, event):
        self.toggle_mark(event.row_key.value)

    @on(Button.Pressed, '#select-all')
    def action_select_all(self):
        if len(self.screen_stack) == 1 and not self.operation:
            self.marked_ids = {item.instance_id for item in self.shown_instances
                               if item.state in TERMINATABLE_STATES}
            self.update_marks()

    @on(Button.Pressed, '#clear-marks')
    def action_clear_marks(self):
        if len(self.screen_stack) == 1 and not self.operation:
            self.marked_ids.clear()
            self.update_marks()

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

    @on(Button.Pressed, '#ssh')
    def action_ssh(self):
        if len(self.screen_stack) != 1 or self.operation:
            return
        instance = self.selected_instance
        if instance is None or instance.state != 'running' or self.refresh_error:
            self.notify('Highlight a running instance in a refreshed table to use SSH.', severity='warning')
            return
        self.operation = f'SSH to {instance.hostname} ({instance.instance_id})'
        self.ssh_active = True
        self.update_selection()
        self.update_refresh_status()
        self.connect_ssh(instance)

    @work(group='operation')
    async def connect_ssh(self, instance):
        try:
            host = await asyncio.to_thread(self.service.ssh_address, instance, private=self.ssh_private_ip)
            command = ssh_command(host, self.ssh_user, self.ssh_key)
            self.log_message(f'SSH to {instance.hostname} ({instance.instance_id}) at {host}.', reveal=False)
            failure = None
            with self.suspend():
                try:
                    print(f'\nSSH to {instance.instance_id} at {host} as {self.ssh_user!r}.')
                    print('Type exit or press Ctrl+D to return to the dashboard.\n', flush=True)
                    code = run_ssh(command)
                except BaseException as exc:
                    # Let Textual resume before propagating an error, including
                    # cancellation. Its suspend context resumes after the yield.
                    failure = exc
            if failure is not None:
                raise failure
            if code == 0:
                self.log_message(f'SSH session closed for {instance.hostname}.', reveal=False)
            else:
                self.log_message(f'SSH for {instance.hostname} exited with status {code}.')
                self.notify(f'SSH exited with status {code}. See Activity.', severity='warning')
        except Exception as exc:
            self.log_message(f'SSH failed for {instance.hostname}: {exc}')
            self.notify('SSH failed. See Activity.', severity='error')
        finally:
            self.ssh_active = False
            self.operation = ''
            self.update_selection()
            self.update_refresh_status()
            self.table.focus()
            if self.refreshing:
                self.ssh_refresh_pending = True
            else:
                self.action_refresh_instances()

    @on(Button.Pressed, '#reboot')
    def action_reboot(self):
        self.confirm_selection('reboot')

    @on(Button.Pressed, '#start')
    def action_start(self):
        self.confirm_selection('start')

    @on(Button.Pressed, '#stop')
    def action_stop(self):
        self.confirm_selection('stop')

    @on(Button.Pressed, '#terminate')
    def action_terminate_selected(self):
        self.confirm_selection('terminate')

    @staticmethod
    def state_requirement(action):
        if action == 'terminate':
            return 'Select instances that have not begun shutting down.'
        state = next(iter(ACTION_STATES[action]))
        return f'{action.capitalize()} requires every selected instance to be {state}.'

    def confirm_selection(self, action):
        if len(self.screen_stack) != 1 or self.operation:
            return
        instances = self.marked_instances
        if not instances or self.refresh_error:
            self.notify(f'Select instances in a refreshed table before choosing {action}.', severity='warning')
            return
        if any(item.state not in ACTION_STATES[action] for item in instances):
            self.notify(self.state_requirement(action), severity='warning')
            return

        def confirmed(accepted):
            if accepted:
                self.operation = f'{action.capitalize()} request for {len(instances)} instances in {instances[0].group}'
                if action == 'terminate':
                    # Keep the empty group selected after its last instance leaves.
                    self.remembered_groups.add(instances[0].group)
                self.update_selection()
                self.update_refresh_status()
                self.change_selected_instances(action, instances)

        self.push_screen(ConfirmSelection(action, instances), confirmed)

    @work(group='operation')
    async def change_selected_instances(self, action, instances):
        label = 'Termination' if action == 'terminate' else action.capitalize()
        try:
            operation = getattr(self.service, f'{action}_instances')
            await asyncio.to_thread(operation, instances)
            for instance in instances:
                self.log_message(f'{label} requested for {instance.hostname} ({instance.instance_id}).')
            if action == 'reboot':
                self.log_message('EC2 may still show running during a reboot; allow a few minutes for recovery.')
            self.marked_ids.difference_update(instance.instance_id for instance in instances)
            self.update_marks()
        except Exception as exc:
            state = {'start': 'starting', 'stop': 'stopping',
                     'reboot': 'rebooting', 'terminate': 'terminating'}[action]
            self.log_message(f'{label} failed: {exc}. Some instances may already be {state}. '
                             'Refresh the table before retrying.')
            self.notify(f'{label} failed. See Activity and refresh before retrying.', severity='error')
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
    parser.add_argument('--ssh-user', default=SSH_USER,
                        help=f'SSH login username (default: {SSH_USER})')
    parser.add_argument('--ssh-key', default=SSH_KEY, metavar='PATH',
                        help='SSH identity file; an empty value uses SSH configuration and agent keys')
    parser.add_argument('--ssh-private-ip', action='store_true',
                        help='Use private IPs for SSH (requires network access to the VPC)')
    args = parser.parse_args(argv)
    if not 5 <= args.refresh_interval < float('inf'):
        parser.error('--refresh-interval must be a finite number of at least 5 seconds')
    if not args.ssh_user.strip():
        parser.error('--ssh-user must not be empty')
    Dashboard(group=args.group, refresh_interval=args.refresh_interval,
              ssh_user=args.ssh_user, ssh_key=args.ssh_key, ssh_private_ip=args.ssh_private_ip).run()


if __name__ == '__main__':
    main()
