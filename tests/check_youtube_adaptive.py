"""Independent YouTube quality selection, segment reads and cancellation; no accounts."""
import argparse
from collections import deque
import importlib
import io
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

parser = argparse.ArgumentParser()
parser.add_argument('--host', type=Path, required=True)
args, remaining = parser.parse_known_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ['DDM_NO_SAVE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('youtube_fixture')
package.__path__ = [str(ROOT / 'plugins/youtube_live'), str(ROOT / 'plugins/global_live')]
sys.modules[package.__name__] = package
playback = importlib.import_module('youtube_fixture.youtube_playback')
adaptive = importlib.import_module('youtube_fixture.youtube_adaptive')
tools = importlib.import_module('youtube_fixture.youtube_tools')
runtime = importlib.import_module('youtube_fixture.youtube_runtime')


def media(itag=308):
    return f'https://rr1.googlevideo.com/videoplayback?itag={itag}&token=fixture'


def formats():
    return {'formats': [
        dict(format_id='308', url=media(), height=1440, fps=60, tbr=8000,
             vcodec='vp9', acodec='none', protocol='http_dash_segments_generator', target_duration=2),
        dict(format_id='298', url=media(298), height=720, fps=60, tbr=2000,
             vcodec='avc1.4d', acodec='none', protocol='https'),
        dict(format_id='140', url=media(140), vcodec='none', acodec='mp4a.40.2',
             tbr=128, protocol='https')]}


class QualityTests(unittest.TestCase):
    def test_real_layout_exposes_1440p60_with_audio_and_bandwidth(self):
        options = playback.quality_formats(formats())
        self.assertEqual([f['label'] for f in options], ['1440p60', '720p60'])
        self.assertEqual(options[0]['qn'], 10000)
        self.assertEqual(options[0]['bandwidth'], 8128000)
        self.assertEqual(options[0]['audio'], media(140))

    def test_missing_audio_never_advertises_unplayable_2k(self):
        info = formats()
        info['formats'].pop()
        self.assertEqual(playback.quality_formats(info), [])

    def test_foreign_and_invalid_urls_are_rejected(self):
        for url in ('http://rr.googlevideo.com/videoplayback', 'https://googlevideo.com.evil/videoplayback',
                    'https://user:password@rr.googlevideo.com/videoplayback',
                    'https://rr.googlevideo.com:444/videoplayback', 'https://rr.googlevideo.com/other'):
            with self.subTest(url=url), self.assertRaises((ValueError, OSError)):
                adaptive.media_url(url)

    def test_duplicate_resolution_prefers_vp9_and_qn_remains_stable(self):
        info = formats()
        alternative = dict(info['formats'][0], vcodec='avc1.4d', tbr=10000)
        info['formats'].insert(0, alternative)
        options = playback.quality_formats(info)
        self.assertEqual(len(options), 2)
        self.assertIn('VP9', options[0]['desc'])
        self.assertEqual(options[1]['qn'], playback.quality_formats(formats())[1]['qn'])

    def test_select_auto_and_preview_lowest_manual_2k_and_cancel(self):
        for quality, preview, expected in ((10000, False, 10000),
                (playback.AUTO_QUALITY, False, None), (10000, True, None)):
            context, reader = Mock(), Mock()
            context.retain_media_source.return_value = 'http://127.0.0.1/media/fixture'
            provider = playback.YouTubePlayback(context)
            with patch.object(playback, 'ensure_runtime'), patch.object(playback, 'extract_live', return_value=formats()), \
                 patch.object(playback, 'request_proxy', return_value=''), patch.object(playback, 'AdaptiveReader', return_value=reader):
                result = provider.play_url('youtube:dklfSqHUu4c', quality, preview=preview)
            self.assertEqual(result[1], expected or provider._qualities['youtube:dklfSqHUu4c'][-1]['qn'])
            context.retain_media_source.assert_called_once()

    def test_failed_reader_invalidates_short_metadata_cache(self):
        provider = playback.YouTubePlayback(Mock())
        with patch.object(playback, 'ensure_runtime'), patch.object(playback, 'extract_live', return_value=formats()), \
             patch.object(playback, 'request_proxy', return_value=''), patch.object(playback, 'AdaptiveReader', side_effect=OSError('expired')):
            with self.assertRaises(OSError):
                provider.play_url('youtube:dklfSqHUu4c')
        self.assertFalse(provider._metadata)

    def test_first_open_can_keep_hls_but_manual_advertised_2k_never_silently_downgrades(self):
        provider = playback.YouTubePlayback(Mock())
        legacy = ('https://fixture/live.m3u8', 10000, 'youtube', {})
        with patch.object(playback, 'ensure_runtime', side_effect=tools.YouTubeMediaError('network')), \
             patch.object(playback, 'request_proxy', return_value=''), \
             patch.object(playback.YouTubePlatform, 'play_url', return_value=legacy) as hls:
            self.assertEqual(provider.play_url('youtube:dklfSqHUu4c'), legacy)
            provider._qualities['youtube:dklfSqHUu4c'] = playback.quality_formats(formats())
            with self.assertRaises(tools.YouTubeMediaError):
                provider.play_url('youtube:dklfSqHUu4c', 10000)
            self.assertEqual(hls.call_count, 1)


class SegmentTests(unittest.TestCase):
    def track(self, response):
        session = MagicMock()
        session.get.return_value.__enter__.return_value = response
        return adaptive.Track(media(), 41, 2, 'http://proxy:7890', threading.Event(), deque(), session=session)

    def test_sequence_replaces_existing_parameter_only(self):
        url = adaptive.sequence_url(media() + '&sq=0&other=keep', 41)
        self.assertEqual(url.count('sq='), 1)
        self.assertIn('sq=41', url)
        self.assertIn('other=keep', url)

    def test_stream_preserves_partial_read_and_advances_sequence(self):
        response = Mock(status_code=200, headers={'X-Head-Seqnum': '45'})
        response.iter_content.return_value = iter([b'abc', b'def'])
        track = self.track(response)
        self.assertEqual(track.read(2), b'ab')
        self.assertEqual(track.read(10), b'cdef')
        self.assertEqual(track.number, 42)
        self.assertFalse(track._session.trust_env)
        self.assertEqual(track._session.get.call_args.kwargs['proxies']['https'], 'http://proxy:7890')
        track.close()
        self.assertEqual(track.read(), b'')

    def test_expired_and_missing_sequence_fail_without_leaking_url(self):
        for status, headers in ((403, {}), (200, {})):
            track = self.track(Mock(status_code=status, headers=headers))
            with self.assertRaises(OSError) as error:
                track.read(1)
            self.assertNotIn('token', str(error.exception))
            track.close()

    def test_head_probe_tolerates_missing_initial_segment(self):
        for status in (200, 404):
            response = Mock(status_code=status, headers={'X-Head-Seqnum': '99'})
            track = self.track(response)
            self.assertEqual(track._segment(0, probe=True), (99, b''))
            response.iter_content.assert_not_called()
            track.close()

    def test_stopped_request_does_not_access_network(self):
        track = self.track(Mock())
        track._stop.set()
        self.assertEqual(track.read(), b'')
        track._session.get.assert_not_called()
        track.close()

    def test_future_segment_waits_for_publication(self):
        track = self.track(Mock())
        track.duration = .001
        future = Mock(status_code=404, headers={'X-Head-Seqnum': '40'})
        ready = Mock(status_code=200, headers={'X-Head-Seqnum': '41'})
        ready.iter_content.return_value = iter([b'published'])
        track._session.get.return_value.__enter__.side_effect = [future, ready]
        self.assertEqual(track.read(), b'published')
        self.assertEqual(track._session.get.call_count, 2)
        track.close()

    def test_mux_starts_at_common_audio_and_video_head(self):
        video, audio, mux = Mock(), Mock(), Mock()
        video._segment.return_value = (105, b'')
        audio._segment.return_value = (100, b'')
        mux.open.return_value = mux
        mux.process.stdout.read1.return_value = b'matroska-header'
        with patch.object(adaptive, 'Track', side_effect=[video, audio]), \
             patch.object(adaptive, 'Muxer', return_value=mux):
            reader = adaptive.AdaptiveReader(media(), media(140), 2, '', 'ffmpeg')
        self.assertEqual(video.number, 98)
        self.assertEqual(audio.number, 98)
        reader.close()


class IsolationTests(unittest.TestCase):
    def test_mux_cleanup_needs_no_new_executor_during_process_shutdown(self):
        mux = adaptive.Muxer.__new__(adaptive.Muxer)
        mux.process = Mock()
        mux.process.poll.return_value = None
        stream, pipe, thread = Mock(), Mock(), Mock()
        mux.streams, mux.pipes, mux.pipe_threads = [stream], [pipe], [thread]
        with patch('concurrent.futures.ThreadPoolExecutor', side_effect=RuntimeError('interpreter shutdown')):
            mux.close()
        mux.process.kill.assert_called_once()
        mux.process.wait.assert_called_once()
        stream.close.assert_called_once()
        pipe.close.assert_called_once()
        thread.join.assert_called_once()
        self.assertTrue(mux.closed)

    def test_fixed_errors_hide_extractor_urls_and_do_not_offer_unused_login(self):
        text = tools.failure_message('Sign in to confirm not a bot: https://private/?token=secret')
        self.assertNotIn('secret', text)
        self.assertNotIn('网页登录', text)

    def test_provider_bundle_integrity_and_private_script_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runtime.unpack_provider(ROOT / 'plugins/youtube_live/youtube-provider.zip', Path(directory))
            scripts = Path(directory) / 'provider/plugins/script/yt_dlp_plugins/extractor'
            self.assertEqual({p.name for p in scripts.iterdir()}, {'getpot_bgutil.py', 'getpot_bgutil_script.py'})
            self.assertTrue((Path(directory) / 'provider/server/build/generate_once.js').is_file())

    def test_legacy_and_sibling_plugins_have_no_adaptive_modules(self):
        self.assertNotIn('youtube_playback', (ROOT / 'plugins/global_live/plugin.py').read_text(encoding='utf-8'))
        for plugin_id in ('global_live', 'twitch_live', 'chzzk_live'):
            self.assertFalse((ROOT / 'plugins' / plugin_id / 'youtube_adaptive.py').exists())


unittest.main(argv=[sys.argv[0], *remaining])
