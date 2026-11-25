"""Local stdio MCP for the opt-in Match Sync Qt Quick diagnostic session."""
import argparse
from pathlib import Path
import json
import time
from typing import Literal

from mcp.server.fastmcp import FastMCP
from PySide6.QtNetwork import QLocalSocket

from telemetry import latest, scene, summarize


def control(pid, request):
    peer = QLocalSocket()
    peer.connectToServer(f'ddm-match-monitor-{pid}')
    if not peer.waitForConnected(650):
        raise ValueError('Control bridge unavailable. Open the match viewer in a new Quick diagnostic process.')
    try:
        peer.write(json.dumps(request).encode('ascii') + b'\n')
        peer.flush()
        deadline = time.monotonic() + 2
        while not peer.canReadLine():
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or not peer.waitForReadyRead(remaining) and not peer.canReadLine():
                raise ValueError('Control bridge timed out')
        response = json.loads(bytes(peer.readLine()))
        if 'error' in response:
            raise ValueError(response['error'])
        return response['state']
    finally:
        peer.abort()


def make_server(logs):
    server = FastMCP('Match Sync diagnostics')
    active = {}

    @server.tool()
    def get_scene(pid: int | None = None) -> dict:
        """Read current live workload and the visible monitor intersection in logical pixels."""
        result = scene(latest(logs, pid))
        try:
            result['controls'] = control(result['pid'], {'command': 'state'})
        except ValueError as error:
            result['controls'] = {'available': False, 'reason': str(error)}
        return result

    @server.tool()
    def set_presenter(pid: int, backend: Literal['raster', 'd3d11']) -> dict:
        """Select a main renderer in this diagnostic viewer and verify the actual backend."""
        state = control(pid, {'command': 'presenter', 'backend': backend})
        deadline = time.monotonic() + 5
        while state['actual'] != backend and state['requested'] == backend and time.monotonic() < deadline:
            time.sleep(.08)
            state = control(pid, {'command': 'state'})
        return dict(applied=state['actual'] == backend and state['requested'] == backend, **state)

    @server.tool()
    def set_scrolling(pid: int, enabled: bool) -> dict:
        """Toggle the match picture's danmaku button through its existing Qt handler."""
        return control(pid, {'command': 'scrolling', 'enabled': enabled})

    @server.tool()
    def list_visible_buttons(pid: int) -> dict:
        """Inventory visible Qt host and workspace buttons by code role without reading their labels."""
        return control(pid, dict(command='inventory'))

    @server.tool()
    def get_button_timings(pid: int) -> dict:
        """Read recent real button releases and diagnostic clicks, including available paint times."""
        return control(pid, {'command': 'buttons'})

    @server.tool()
    def set_comparison_open(pid: int, opened: bool) -> dict:
        """Use the comparison button once if necessary and time its synchronous Qt click."""
        return control(pid, {'command': 'comparison', 'opened': opened})

    @server.tool()
    def set_precise_wait(pid: int, enabled: bool) -> dict:
        """A/B the active D3D11 worker's four-millisecond wait without restarting playback."""
        return control(pid, dict(command='precise_wait', enabled=enabled))

    @server.tool()
    def hold_periodic_search(pid: int, seconds: int) -> dict:
        """Temporarily suppress new timer-driven match searches while playback and admission continue."""
        return control(pid, dict(command='hold_search', seconds=seconds))

    @server.tool()
    def set_raster_paint(pid: int, immediate: bool) -> dict:
        """Compare synchronous and queued main-canvas painting during the raster trial."""
        return control(pid, dict(command='raster_paint', enabled=immediate))

    @server.tool()
    def set_row_toggle(pid: int, source: int, control_name: Literal['audio', 'left', 'right', 'chat', 'assist'],
                       checked: bool) -> dict:
        """Click the selected row control when its state differs, and return the click timing."""
        return control(pid, dict(command='row_toggle', source=source, control=control_name, checked=checked))

    @server.tool()
    def set_main_paused(pid: int, paused: bool) -> dict:
        """Time pause or resume using the main playback button."""
        return control(pid, dict(command='pause_picture', paused=paused))

    @server.tool()
    def probe_follow_folders(pid: int, bulk: bool) -> dict:
        """Measure the synchronous callbacks and restore every folder's original fold state."""
        return control(pid, dict(command='probe_folders', bulk=bulk))

    @server.tool()
    def set_follow_folder(pid: int, bulk: bool, collapsed: bool) -> dict:
        """Time the folder header or fold-all control in a single-folder sidebar."""
        return control(pid, dict(command='follow_folder', all=bulk, collapsed=collapsed))

    @server.tool()
    def trial_short_switch(pid: int, seconds: int) -> dict:
        """Temporarily shorten the diagnostic process's Python thread switch interval. Zero restores it."""
        return control(pid, dict(command='short_switch', seconds=seconds))

    @server.tool()
    def set_follow_filter(pid: int, live_only: bool) -> dict:
        """Time the live or all filter when the follow list is visible."""
        return control(pid, dict(command='follow_filter', live_only=live_only))

    @server.tool()
    def set_sidebar_multiselect(pid: int, enabled: bool) -> dict:
        """Exercise the multiselect and cancel controls on an idle sidebar."""
        return control(pid, dict(command='multiselect', enabled=enabled))

    @server.tool()
    def set_sidebar_collapsed(pid: int, collapsed: bool) -> dict:
        """Time the host sidebar fold or unfold button through the current trial workspace."""
        return control(pid, dict(command='sidebar', collapsed=collapsed))

    @server.tool()
    def set_console_open(pid: int, panel: Literal['playback', 'comments', 'sender'], opened: bool) -> dict:
        """Exercise the playback, comment list or composer collapse and restore buttons."""
        return control(pid, dict(command='console', panel=panel, opened=opened))

    @server.tool()
    def get_sources(pid: int) -> dict:
        """Read numeric quality choices and synchronization state by source slot."""
        return control(pid, dict(command='sources'))

    @server.tool()
    def set_main_quality(pid: int, quality: int) -> dict:
        """Select an available unrestricted main quality using the player's quality handler."""
        return control(pid, dict(command='main_quality', quality=quality))

    @server.tool()
    def set_independent(pid: int, source: int, enabled: bool) -> dict:
        """Select independent playback for one secondary source through the existing handler."""
        return control(pid, dict(command='independent', source=source, enabled=enabled))

    @server.tool()
    def probe_panel(pid: int, action: Literal['pip', 'pip_volume', 'region', 'clock', 'plugin_settings', 'settings',
                                               'add_room', 'notifications', 'layout', 'wall_settings', 'host_pip',
                                               'manage', 'monitor', 'functions'],
                    source: int = 0, profile: bool = False) -> dict:
        """Open a visible control, observe its panel's first paint, and dismiss that panel.
        A separately hosted settings window needs external visibility sampling and dismissal.
        """
        control(pid, dict(command='profile_panel' if profile else 'probe_panel', action=action, source=source))
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                time.sleep(.12)
                if 'panel_first_paint_ms' in control(pid, dict(command='panel_result')):
                    break
        finally:
            result = control(pid, dict(command='finish_panel'))
        return result

    @server.tool()
    def confirm_mix(pid: int, source: int) -> dict:
        """Press the visible admission button for a matched pending secondary source."""
        return control(pid, dict(command='confirm_mix', source=source))

    @server.tool()
    def get_crop_layout(pid: int) -> dict:
        """Read exact trial PiP rectangles and logical desktop positions for restore_crop."""
        return control(pid, dict(command='crops'))

    @server.tool()
    def restore_crop(pid: int, source: int, slot: int, rect: list[float], placement: list[int]) -> dict:
        """Restore a trial PiP by room index, normalized source rectangle and global logical x,y,width."""
        return control(pid, dict(command='crop', source=source, slot=slot, rect=rect, placement=placement))

    @server.tool()
    def start_sample(pid: int) -> dict:
        """Mark the start of a playback/Quick timing sample. Keep this stdio session open."""
        if pid in active:
            raise ValueError('A sample is already active for this process')
        initial = scene(latest(logs, pid))
        try:
            window = control(pid, {'command': 'state'})['quick_window_id']
        except ValueError:
            window = None
        active[pid] = (time.time(), initial, window)
        return dict(started=True, scene=initial)

    @server.tool()
    def stop_sample(pid: int) -> dict:
        """Summarize matching diagnostic buckets. Quick swaps measure renderer cadence."""
        if pid not in active:
            raise ValueError('No active sample for this process')
        start, initial, window = active[pid]
        result = summarize(logs, start, time.time(), initial, window)
        del active[pid]
        return result

    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    make_server(args.host.resolve() / 'logs').run(transport='stdio')
