"""Validate room links and relay only to a local, token-protected GUI queue."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import threading
from urllib.parse import urlsplit, urlunsplit


ROOM_HOSTS = {'live.bilibili.com','b23.tv','www.b23.tv','www.huya.com','huya.com',
    'www.douyu.com','douyu.com','live.douyin.com','www.douyin.com','www.twitch.tv','twitch.tv',
    'www.youtube.com','youtube.com','youtu.be'}


def room_link(uri):
    if not isinstance(uri,str) or len(uri)>4096 or any(ord(char)<32 for char in uri):
        raise ValueError('快捷链接无效或过长')
    parts = urlsplit(uri)
    if (parts.scheme!='ddcut' or parts.hostname not in ROOM_HOSTS or parts.username or parts.password
            or parts.port is not None or not parts.path.strip('/')):
        raise ValueError('请将支持的直播间链接中的https://替换为ddcut://')
    return urlunsplit(('https',parts.hostname,parts.path,parts.query,''))


class LinkServer:
    def __init__(self, endpoint):
        self.endpoint = Path(endpoint)
        self.requests = queue.Queue(maxsize=16)
        self.token = secrets.token_hex(24)
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):
                pass

            def do_POST(self):
                key = self.headers.get('X-DDCut-Key','')
                if (self.path!='/link' or self.headers.get('Origin') or
                        not key.isascii() or not secrets.compare_digest(key,owner.token)):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0<size<=8192 or self.headers.get_content_type()!='application/json':
                        raise ValueError('无效请求')
                    value = json.loads(self.rfile.read(size))
                    link = room_link(value['uri'])
                    owner.requests.put_nowait(link)
                except queue.Full:
                    self.send_error(429)
                    return
                except (ValueError,KeyError,TypeError):
                    self.send_error(400)
                    return
                self.send_response(202)
                self.send_header('Content-Length','0')
                self.end_headers()

        self.http = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=lambda:self.http.serve_forever(poll_interval=.1),name='ddcut-room-links',daemon=True)
        try:
            self.endpoint.parent.mkdir(parents=True,exist_ok=True)
            temporary = self.endpoint.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(port=self.http.server_port,token=self.token)),encoding='utf-8')
            temporary.replace(self.endpoint)
        except OSError:
            self.http.server_close()
            raise
        self.thread.start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        try:
            if json.loads(self.endpoint.read_text(encoding='utf-8'))['token']==self.token:
                self.endpoint.unlink()
        except FileNotFoundError:
            pass
