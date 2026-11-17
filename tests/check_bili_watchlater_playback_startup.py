"""Paused startup readiness and bounded, credential-free audio evidence."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from check_bili_watchlater import plugin_module, models
from ddm_plugin_bili_watchlater import acceptance_trace as trace


class StartupTests(unittest.TestCase):
    def make_playback(self, *, ready=True, position=30., duration=60.):
        owner = plugin_module.BiliWatchLaterPlugin()
        item = models.WatchLaterItem(bvid='BV1xx411c7mD')
        tile = Mock(room={'watchlater_bvid': item.bvid}, paused=True, _player_active=False)
        tile.recording_button = None
        binding = plugin_module._VodBinding(item, object(), tile)
        binding.state = 'paused'
        binding.stream = SimpleNamespace(is_dash=True, audio_url='secret-audio-url')
        properties = {'duration': duration, 'time-pos': position, 'vo-configured': ready}
        player = Mock()
        player._prop.side_effect = lambda key, default=None: properties.get(key, default)
        owner._host_player = lambda _: player
        for name in ('_sync_fullscreen_state', '_sync_subtitle_control', '_sync_position_badge'):
            setattr(owner, name, Mock())
        owner.playback.apply_subtitle = Mock()
        owner.playback.maybe_prefetch = Mock()
        return owner, binding, tile, player, properties

    def test_unknown_initial_time_preserves_saved_progress_and_duration(self):
        owner, binding, tile, _, _ = self.make_playback(ready=False, position=None, duration=0.)
        binding.progress, binding.duration = 123., 600.
        widget = Mock()
        owner._bar_progress[id(tile)] = dict(widget=widget, visible=True)
        with patch.object(trace, 'record'):
            owner.playback.tick_binding(binding, [tile])
        widget.set_position.assert_called_once_with(123.)
        widget.set_duration.assert_called_once_with(600.)
        self.assertEqual(binding.progress, 123.)

    def test_restore_seek_keeps_saved_time_until_player_acknowledges_it(self):
        owner, binding, tile, _, props = self.make_playback(ready=False, position=0.)
        binding.progress = binding.pending_seek = 123.
        widget = Mock()
        owner._bar_progress[id(tile)] = dict(widget=widget, visible=True)
        owner.playback.mpv_command = Mock(return_value=True)
        with patch.object(trace, 'record'):
            for position, seeking in ((0., True), (0., False), (123., False)):
                props.update({'time-pos': position, 'seeking': seeking})
                owner.playback.tick_binding(binding, [tile])
                self.assertEqual(binding.progress, 123.)
        self.assertEqual([call.args[0] for call in widget.set_position.call_args_list], [123., 123., 123.])
        owner.playback.mpv_command.assert_called_once_with(owner._host_player(tile), 'seek', 123., 'absolute')
        self.assertEqual(binding.pending_seek, 0.)

    def test_actual_zero_without_pending_restore_is_not_hidden(self):
        owner, binding, tile, _, _ = self.make_playback(position=0.)
        binding.progress = 123.
        with patch.object(trace, 'record'):
            owner.playback.tick_binding(binding, [tile])
        self.assertEqual(binding.progress, 0.)

    def test_manual_seek_to_zero_cancels_pending_restore(self):
        owner, binding, tile, player, _ = self.make_playback(position=0.)
        binding.pending_seek, binding.seek_issued = 123., True
        owner._player_for_binding = lambda _: player
        owner.playback.mpv_command = Mock(return_value=True)
        owner.playback.seek_binding(binding, 0.)
        with patch.object(trace, 'record'):
            owner.playback.tick_binding(binding, [tile])
        self.assertEqual((binding.pending_seek, binding.progress), (0., 0.))
        self.assertFalse(binding.seek_issued)
        owner.playback.mpv_command.assert_called_once_with(player, 'seek', 0., 'absolute')

    def test_closed_reserved_slot_rejects_late_stream_even_with_same_empty_room_id(self):
        owner, binding, tile, _, _ = self.make_playback()
        tile.room = {}
        binding.request_id = 7
        owner._host_tiles = lambda: [tile]
        owner._bindings[binding.bvid] = binding
        owner.playback.cell_vod[id(binding.cell)] = 7
        owner.playback.vod_context[7] = dict(tile=tile, cell=binding.cell, generation=0,
            bvid=binding.bvid, expected_room='', expected_video=binding.bvid)
        with patch.object(owner.playback, 'inject_playback') as inject:
            owner.playback.on_vod_completed(7, 0, object())
        inject.assert_not_called()

    def test_failed_restore_seek_warns_once_and_does_not_retry_forever(self):
        owner, binding, tile, _, _ = self.make_playback(position=0.)
        binding.pending_seek = binding.progress = 123.
        owner.playback.mpv_command = Mock(return_value=False)
        owner._notify_warning = Mock()
        with patch.object(trace, 'record'):
            owner.playback.tick_binding(binding, [tile])
            owner.playback.tick_binding(binding, [tile])
        owner._notify_warning.assert_called_once()
        self.assertEqual(owner.playback.mpv_command.call_count, 1)

    def test_paused_loaded_frame_clears_connecting_without_resuming(self):
        owner, binding, tile, player, _ = self.make_playback()
        with patch.object(trace, 'record'):
            owner.playback.tick_binding(binding, [tile])
        tile.set_video_active.assert_called_once_with(True)
        tile.set_buffering.assert_called_once_with(False)
        tile.set_status.assert_called_once_with('')
        tile.set_paused.assert_called_once_with(True)
        player.set_paused.assert_not_called()
        self.assertEqual(binding.state, 'paused')

    def test_metadata_or_position_alone_does_not_claim_a_ready_frame(self):
        for ready, position in ((False, 30.), (True, None)):
            with self.subTest(ready=ready, position=position):
                owner, binding, tile, _, _ = self.make_playback(ready=ready, position=position)
                with patch.object(trace, 'record'):
                    owner.playback.tick_binding(binding, [tile])
                tile.set_video_active.assert_not_called()
                tile.set_status.assert_not_called()

    def test_active_paused_frame_does_not_repeat_ui_transition(self):
        owner, binding, tile, _, _ = self.make_playback()
        tile._player_active = True
        with patch.object(trace, 'record'):
            owner.playback.tick_binding(binding, [tile])
        tile.set_status.assert_not_called()

    def test_audio_sample_counts_tracks_without_serializing_identifiers(self):
        _, binding, tile, player, props = self.make_playback()
        props.update({'track-list': [dict(type='audio', selected=True, title='secret-title',
                      external_filename='secret-url'), dict(type='video')],
                      'audio-params': {'format': 'secret-format'},
                      'audio-out-params': {'format': 'secret-format'}, 'volume': 70.})
        with patch.object(trace, 'record') as record:
            for index in range(100):
                tile.paused = index % 2 == 0
                trace.playback_sample(binding, player)
        self.assertEqual(record.call_count, 8)
        first = record.call_args_list[0].kwargs
        self.assertEqual(first['audio_tracks'], 1)
        self.assertEqual(first['selected_audio'], 1)
        self.assertTrue(first['audio_output'])
        self.assertEqual(first['volume'], 70.)
        text = json.dumps([call.kwargs for call in record.call_args_list])
        self.assertNotIn('secret', text)
        self.assertNotIn(binding.bvid, text)


if __name__ == '__main__':
    unittest.main()
