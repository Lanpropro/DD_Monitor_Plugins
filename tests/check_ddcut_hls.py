"""Local HLS HTTP regression: ranges, playlist replacement and shutdown."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / 'plugins/DDCut_Replay'


def module(name):
    spec = importlib.util.spec_from_file_location('ddcut_' + name, ROOT / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


hls = module('hls')
ArchiveServer = module('preview_server').ArchiveServer


class ArchiveHTTPTests(unittest.TestCase):
    def test_growing_playlist_replacement_ranges_and_private_registration(self):
        with tempfile.TemporaryDirectory(prefix='ddcut-hls-http-') as directory:
            root = Path(directory)
            archive = root / 'archive'
            archive.mkdir()
            media = archive / 'part.ts'
            media.write_bytes(bytes(range(100)))
            playlist = archive / 'playlist.m3u8'
            segments = [{'path': str(media), 'duration': 2.4}]
            hls.write_playlist(playlist, segments, complete=False)
            (root / 'outside.ts').write_bytes(b'outside registered archive')
            (archive / 'recording.json').write_text('{}')
            server = ArchiveServer()
            try:
                url = server.source_url(playlist)
                self.assertIn('http://127.0.0.1:', url)
                with urllib.request.urlopen(url, timeout=3) as response:
                    self.assertIn('#EXT-X-PLAYLIST-TYPE:EVENT', response.read().decode())
                # A prior GET must not hold a Windows file handle that prevents
                # the recorder from replacing this same playlist.
                hls.write_playlist(playlist, segments, complete=True)
                with urllib.request.urlopen(url, timeout=3) as response:
                    self.assertIn('#EXT-X-ENDLIST', response.read().decode())
                part_url = url.rsplit('/', 1)[0] + '/part.ts'
                request = urllib.request.Request(part_url, headers={'Range': 'bytes=7-12'})
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers['Content-Range'], 'bytes 7-12/100')
                    self.assertEqual(response.read(), bytes(range(7, 13)))
                request = urllib.request.Request(part_url, method='HEAD')
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.headers['Content-Length'], '100')
                    self.assertEqual(response.read(), b'')
                for tail in ('%2e%2e/outside.ts', 'recording.json', 'missing.ts'):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(url.rsplit('/', 1)[0] + '/' + tail, timeout=3)
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.close()
            self.assertFalse(server.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
