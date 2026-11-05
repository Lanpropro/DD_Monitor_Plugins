"""Loopback-only archive reader, keeping Windows playlist handles short-lived."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit
import uuid


class ArchiveServer:
    def __init__(self):
        self.archives = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.serve(True)

            def do_HEAD(self):
                self.serve(False)

            def serve(self, body):
                parts = unquote(urlsplit(self.path).path).strip('/').split('/', 1)
                root = owner.archives.get(parts[0])
                if root is None or len(parts) != 2:
                    self.send_error(404)
                    return
                path = (root / parts[1]).resolve()
                if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in ('.m3u8', '.ts', '.mp4'):
                    self.send_error(404)
                    return
                try:
                    # In particular, never hold playlist.m3u8 open while the
                    # client consumes its response; Windows must allow replace.
                    data = path.read_bytes()
                    start, end = 0, len(data) - 1
                    requested = self.headers.get('Range')
                    if requested:
                        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                        if not match or not any(match.groups()):
                            self.send_error(416)
                            return
                        left, right = match.groups()
                        start = int(left) if left else max(0, len(data) - int(right))
                        end = min(end, int(right)) if left and right else end
                        if start > end or start >= len(data):
                            self.send_error(416)
                            return
                    self.send_response(206 if requested else 200)
                    self.send_header('Content-Type', 'application/vnd.apple.mpegurl' if path.suffix == '.m3u8' else 'application/octet-stream')
                    self.send_header('Content-Length', str(end - start + 1))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-store')
                    if requested:
                        self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
                    self.end_headers()
                    if body:
                        self.wfile.write(data[start:end + 1])
                except ConnectionError:
                    pass
                except OSError:
                    self.send_error(404)

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.1),
                                       name='ddcut-archive-preview', daemon=True)
        self.thread.start()

    def source_url(self, path):
        path = Path(path)
        if path.suffix.lower() != '.m3u8':
            return str(path)
        root = path.resolve().parent
        token = next((token for token, folder in self.archives.items() if folder == root), None)
        if token is None:
            token = uuid.uuid4().hex
            self.archives[token] = root
        return f'http://127.0.0.1:{self.http.server_port}/{token}/{path.name}'

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.archives.clear()
