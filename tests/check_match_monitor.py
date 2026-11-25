"""Exercise the actual stdio transport. The live read check only samples numeric counters."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(host, pid, seconds, check_controls):
    script = Path(__file__).resolve().parents[1] / 'tools/match_monitor/server.py'
    params = StdioServerParameters(command=sys.executable, args=[str(script), '--host', str(host)],
                                  env=dict(os.environ))
    async with stdio_client(params) as streams, ClientSession(*streams) as client:
        await client.initialize()
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {'get_scene', 'set_presenter', 'set_scrolling', 'start_sample', 'stop_sample',
                         'get_button_timings', 'set_comparison_open', 'restore_crop', 'set_follow_filter', 'trial_short_switch',
                         'set_main_paused', 'set_follow_folder', 'probe_follow_folders',
                         'get_sources', 'set_row_toggle', 'set_console_open', 'set_sidebar_multiselect', 'set_sidebar_collapsed', 'set_raster_paint', 'hold_periodic_search', 'set_main_quality', 'set_independent', 'set_precise_wait', 'get_crop_layout', 'confirm_mix', 'probe_panel', 'list_visible_buttons'}

        async def call(name, **args):
            out = await client.call_tool(name, args)
            if out.isError:
                raise RuntimeError(out.content[0].text)
            return json.loads(out.content[0].text)

        if check_controls:
            assert (await client.call_tool('set_sidebar_collapsed', dict(pid=pid, collapsed=True))).isError
            assert (await client.call_tool('probe_panel', dict(pid=pid, action='host_pip'))).isError
            assert (await client.call_tool('probe_panel', dict(pid=pid, action='functions'))).isError
            assert (await client.call_tool('set_sidebar_multiselect', dict(pid=pid, enabled=True))).isError
            assert (await client.call_tool('set_follow_filter', dict(pid=pid, live_only=True))).isError
            assert (await client.call_tool('set_follow_folder', dict(pid=pid, bulk=True, collapsed=True))).isError
            assert (await client.call_tool('probe_follow_folders', dict(pid=pid, bulk=True))).isError
            paused = await call('hold_periodic_search', pid=pid, seconds=1)
            original = await call('trial_short_switch', pid=pid, seconds=0)
            assert (await call('trial_short_switch', pid=pid, seconds=1))['interval_ms'] == 1
            await asyncio.sleep(1.2)
            # Start again only after verifying automatic restoration via the process state.
            assert (await call('set_presenter', pid=pid, backend='raster'))['switch_interval_ms'] == original['interval_ms']
            assert paused['held']
            assert not (await call('hold_periodic_search', pid=pid, seconds=0))['held']
            assert (await client.call_tool('hold_periodic_search', dict(pid=pid, seconds=999))).isError
            for backend in ('raster', 'd3d11', 'raster'):
                result = await call('set_presenter', pid=pid, backend=backend)
                assert result['applied'] and result['actual'] == backend and result['quick_window_id'], result
                if backend == 'd3d11':
                    for enabled in (True, False, True):
                        assert (await call('set_precise_wait', pid=pid, enabled=enabled))['precise_wait'] is enabled
            assert (await client.call_tool('set_precise_wait', dict(pid=pid, enabled=True))).isError
            for immediate in (True, False):
                assert (await call('set_raster_paint', pid=pid, immediate=immediate))['immediate'] == immediate
            for enabled in (False, True):
                result = await call('set_scrolling', pid=pid, enabled=enabled)
                assert result['scrolling'] is enabled
            for opened in (True, True, False):
                result = await call('set_comparison_open', pid=pid, opened=opened)
                assert result['opened'] is opened
            recent = (await call('get_button_timings', pid=pid))['recent']
            clicks = [row for row in recent if row['source'] == 'mcp_click']
            assert len(clicks) == 2 and clicks[0]['button'] == clicks[1]['button']
            assert all(row['role'] == 'Viewer.compare' and row['click_handler_ms'] >= 0 for row in clicks)
            assert clicks[0]['next_turn_ms'] >= 0
            assert all(row['panel_visibility_ms'] >= 0 and row['splitter_sizes_ms'] >= 0 for row in clicks)
            assert clicks[0]['next_panel_paint_ms'] >= 0
            source = (await call('get_sources', pid=pid))['sources'][0]
            assert source['main'] and not source['independent']
            assert not source['awaiting'] and not source['admitted']
            assert (await client.call_tool('confirm_mix', dict(pid=pid, source=0))).isError
            assert (await client.call_tool('set_main_quality', dict(pid=pid, quality=-1))).isError
            assert not (await call('set_independent', pid=pid, source=0, enabled=False))['independent']
            inventory = (await call('list_visible_buttons', pid=pid))['buttons']
            assert any(b['role'] == 'Viewer.compare' for b in inventory)
            args = dict(pid=pid, source=0, control_name='chat')
            first = await call('set_row_toggle', **args, checked=False)
            repeat = await call('set_row_toggle', **args, checked=False)
            assert not repeat['checked'] and repeat['timing'] is None
            changed = await call('set_row_toggle', **args, checked=True)
            assert changed['checked'] and changed['timing']['role'] == 'RoomRow.show_chat'
            await call('set_row_toggle', **args, checked=first['before'])
            assert (await client.call_tool('set_row_toggle', dict(args, source=-1, checked=True))).isError
            for panel_name in ('sender', 'comments', 'playback'):
                for opened in (False, True):
                    console = await call('set_console_open', pid=pid, panel=panel_name, opened=opened)
                    assert console['opened'] == opened and console['timing']['click_handler_ms'] >= 0, console
                    if not opened:
                        assert 'button_hidden_ms' in console['timing'] and 'next_button_paint_ms' not in console['timing']
            for paused in (True, False):
                result = await call('set_main_paused', pid=pid, paused=paused)
                assert result['paused'] == paused and result['timing']['click_handler_ms'] >= 0
            panel = await call('probe_panel', pid=pid, action='plugin_settings')
            assert 0 <= panel['panel_show_ms'] <= panel['panel_first_paint_ms'], panel
            repeat = await call('probe_panel', pid=pid, action='plugin_settings', profile=True)
            assert repeat['started_at'] > panel['started_at'] and repeat['panel_first_paint_ms'] >= 0
            assert 'profile' not in panel and repeat['profile'][0]['self_ms'] > 0
            assert sum(repeat['qt_events'].values()) > 0 and repeat['window_blocked'] >= 0
            crop = dict(pid=pid, source=0, slot=0, rect=[.25, .25, .5, .5], placement=[100, 200, 320])
            restored = await call('restore_crop', **crop)
            assert restored['rect'] == crop['rect'] and restored['width'] == 320
            saved, = (await call('get_crop_layout', pid=pid))['crops']
            assert saved['source'] == saved['slot'] == 0 and saved['rect'] == crop['rect']
            assert await call('restore_crop', pid=pid, **saved) == restored
            edit = await call('probe_panel', pid=pid, action='pip_volume')
            assert all(edit.get(key) for key in ('volume_changed', 'draft_only', 'volume_restored', 'cancel_preserved')), edit
            assert (await call('get_crop_layout', pid=pid))['crops'] == [saved]
            invalid = await client.call_tool('restore_crop', dict(pid=pid, source=-1, slot=0,
                rect=[0., 0., 1., 1.], placement=[100, 200, 320]))
            assert invalid.isError
            bad = await client.call_tool('set_presenter', {'pid': pid, 'backend': 'invalid'})
            assert bad.isError
            print('MCP raster/D3D11/raster and scrolling off/on verified', flush=True)
            return
        state = await call('get_scene', **({'pid': pid} if pid else {}))
        pid = state['pid']
        await call('start_sample', pid=pid)
        duplicate = await client.call_tool('start_sample', {'pid': pid})
        assert duplicate.isError
        await asyncio.sleep(seconds)
        result = await call('stop_sample', pid=pid)
        assert result['matched_buckets'] > 0, result
        print(json.dumps({'state': state, 'sample': result}, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--seconds', type=int, default=5)
    parser.add_argument('--check-controls', action='store_true', help='Use only on an isolated fixture viewer')
    args = parser.parse_args()
    asyncio.run(run(args.host, args.pid, args.seconds, args.check_controls))
