"""Authenticated RTMP input, async backpressure and HLS archive ownership."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import secrets
import struct
import tempfile
import threading
import time
import uuid

from ddm.recording import safe_name
from .hls import read_segments, write_playlist
from .vendor.pyrtmp.rtmp import SimpleRTMPController
from .vendor.pyrtmp import StreamClosedException
from .vendor.pyrtmp.messages.protocol_control import Acknowledgement
from .vendor.pyrtmp.messages.user_control import PingResponse


class PushArchive:
    def __init__(self, definition, options):
        self.identity = uuid.uuid4().hex
        self.created = time.time()
        name = safe_name(definition['name'])[:48]
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.folder = Path(options['directory'])/'DDCut_Replay'/f'RTMP_{name}_{stamp}_{self.identity[:8]}'
        self.folder.mkdir(parents=True)
        self.path = self.folder/f'RTMP_{name}_{stamp}.m3u8'
        self.room = dict(platform='rtmp', room_id='rtmp:'+definition['id'], uname=definition['name'], title=definition['name'])
        self.ffmpeg = options['ffmpeg']
        self.process = None
        self.log = tempfile.TemporaryFile(dir=self.folder)
        self.last_info = None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(self.ffmpeg, '-hide_banner', '-v', 'error',
            '-f', 'flv', '-i', 'pipe:0', '-map', '0:v:0', '-map', '0:a?', '-c', 'copy',
            '-f', 'hls', '-hls_time', '2', '-hls_list_size', '0', '-hls_playlist_type', 'event',
            '-hls_flags', 'temp_file+program_date_time', '-hls_segment_filename', str(self.folder/'part-%05d.ts'), str(self.path),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=self.log,
            creationflags=0x08000000 if __import__('os').name == 'nt' else 0)
        self.process.stdin.write(b'FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00')
        await self.process.stdin.drain()

    async def write(self, kind, stamp, payload):
        if not self.process:
            raise RuntimeError('尚未开始推流')
        stamp = int(stamp) & 0xffffffff
        header = bytes([kind])+len(payload).to_bytes(3,'big')+(stamp & 0xffffff).to_bytes(3,'big')
        header += bytes([stamp >> 24])+b'\x00\x00\x00'
        self.process.stdin.write(header+payload+struct.pack('>I',11+len(payload)))
        await self.process.stdin.drain()

    def refresh(self, complete=False):
        if not self.path.exists():
            return None
        segments = read_segments(self.path)
        if not segments:
            return None
        if not complete and self.last_info and self.last_info['segments'] == segments:
            return self.last_info
        if complete:
            write_playlist(self.path,segments,complete=True)
        info = dict(path=str(self.path), room=self.room, title=self.room['title'], session_id=self.identity,
            created_at=self.created, source_created_at=self.created, kind='recording', format='ts',
            index_format='hls', video_codec='avc', segments=segments,
            duration=sum(row['duration'] for row in segments), recording=True, complete=complete)
        if complete:
            info['ended_at'] = time.time()
        if (self.folder/'cover.png').is_file():
            info['cover_path'] = str(self.folder/'cover.png')
        temporary = self.folder/'recording.json.tmp'
        temporary.write_text(json.dumps(info,ensure_ascii=False),encoding='utf-8')
        temporary.replace(self.folder/'recording.json')
        self.last_info = info
        return info

    async def close(self):
        error = ''
        try:
            if self.process:
                self.process.stdin.close()
                try:
                    await asyncio.wait_for(self.process.wait(),5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                    error = '推流收尾超时；已保存分段保留'
                if self.process.returncode:
                    self.log.seek(0)
                    error = self.log.read()[-1200:].decode('utf-8',errors='replace') or error
            try:
                return self.refresh(complete=True), error
            except (OSError,ValueError) as failure:
                return None, error or ('回看索引保存失败，原分段保留：'+str(failure))
        finally:
            self.log.close()


class PushController(SimpleRTMPController):
    def __init__(self, owner):
        self.owner = owner

    async def client_callback(self, reader, writer):
        owner = self.owner
        if not owner.definition['enabled'] or len(owner.writers) >= 2:
            writer.close()
            await writer.wait_closed()
            return
        owner.writers.add(writer)
        task = asyncio.current_task()
        owner.tasks.add(task)
        try:
            await super().client_callback(reader, writer)
        finally:
            owner.tasks.discard(task)
            owner.writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def on_handshake(self, session):
        await asyncio.wait_for(session.handshake(),10)

    async def on_nc_connect(self, session, message):
        if message.command_object.get('app') != 'live':
            self.owner.reject('拒绝推流：应用名必须为live')
            raise StreamClosedException()
        await super().on_nc_connect(session,message)

    async def on_ns_publish(self, session, message):
        owner = self.owner
        if (not owner.definition['enabled'] or owner.active is not None or
                not secrets.compare_digest(message.publishing_name,owner.definition['key'])):
            owner.reject('拒绝推流：密钥不正确、房间已停用或已有推流')
            raise StreamClosedException()
        owner.active = session
        owner.failure = ''
        try:
            owner.archive = PushArchive(owner.definition,owner.options)
            await owner.archive.start()
            owner.update('recording')
            await super().on_ns_publish(session,message)
        except Exception:
            await self.cleanup(session)
            raise

    async def on_metadata(self, session, message):
        if session is self.owner.active:
            await self.owner.archive.write(18, message.timestamp, message.to_raw_meta())

    async def on_window_acknowledgement_size(self, session, message):
        session.state['ack_window'] = max(1,message.ack_window_size)

    async def acknowledge(self, session):
        # PyRTMP's reader counter is in bits despite its property name.
        received = session.total_read_bytes//8
        if received-session.state.get('ack_sent',0) >= session.state.get('ack_window',2500000):
            session.write_chunk_to_stream(Acknowledgement(received & 0xffffffff))
            await session.drain()
            session.state['ack_sent'] = received

    async def on_video_message(self, session, message):
        if session is self.owner.active:
            if not message.payload or message.payload[0] & 15 != 7:
                self.owner.failure = 'RTMP输入当前支持H.264视频，请调整OBS编码设置'
                raise StreamClosedException()
            await self.owner.archive.write(9,message.timestamp,message.payload)
            await self.acknowledge(session)

    async def on_audio_message(self, session, message):
        if session is self.owner.active:
            if not message.payload or message.payload[0] >> 4 != 10:
                self.owner.failure = 'RTMP输入当前支持AAC音频，请调整OBS编码设置'
                raise StreamClosedException()
            await self.owner.archive.write(8,message.timestamp,message.payload)
            await self.acknowledge(session)

    async def on_ns_close_stream(self, session, message):
        session.writer.close()

    async def on_ns_delete_stream(self, session, message):
        session.writer.close()

    async def on_unknown_message(self, session, message):
        if message.msg_type_id==4 and len(message.payload)>=6 and message.payload[:2]==b'\x00\x06':
            session.write_chunk_to_stream(PingResponse(int.from_bytes(message.payload[2:6],'big')))
            await session.drain()

    async def cleanup(self, session):
        owner = self.owner
        if session is owner.active:
            archive, owner.archive = owner.archive, None
            owner.active = None
            info, error = await archive.close() if archive else (None,'无法启动推流归档')
            if info:
                owner.events.put(info)
            owner.update('waiting' if owner.definition['enabled'] else 'disabled', error=error or owner.failure)


class RtmpReceiver:
    """A room owns its port, event loop, connections and one active publisher."""
    def __init__(self, definition, options, events):
        self.definition, self.options, self.events = deepcopy(definition), deepcopy(options), events
        self.lock = threading.Lock()
        self.state = dict(state='starting', error='', info=None)
        self.archive = self.active = self.server = None
        self.failure = ''
        self.writers, self.tasks = set(), set()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever,name='ddcut-rtmp',daemon=True)
        self.thread.start()
        try:
            self.submit(self.start()).result(timeout=10)
        except Exception:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
            self.loop.close()
            raise

    def submit(self, operation):
        return asyncio.run_coroutine_threadsafe(operation,self.loop)

    async def start(self):
        controller = PushController(self)
        self.server = await asyncio.start_server(controller.client_callback,self.definition.get('host','127.0.0.1'),self.definition['port'])
        self.definition['port'] = self.server.sockets[0].getsockname()[1]
        self.update('waiting' if self.definition['enabled'] else 'disabled')
        self.refresh_task = asyncio.create_task(self.refresh())

    def update(self, state, *, info=None, error=''):
        with self.lock:
            self.state = dict(state=state,info=info,error=error)

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

    def reject(self, error):
        with self.lock:
            self.state['error'] = error

    async def refresh(self):
        while True:
            await asyncio.sleep(.5)
            if self.archive:
                try:
                    info = self.archive.refresh()
                    if info:
                        self.update('recording',info=info)
                except (OSError,ValueError) as error:
                    self.update('recording',error=str(error))

    async def enable(self, value):
        self.definition['enabled'] = bool(value)
        if not value:
            for writer in tuple(self.writers):
                writer.transport.abort()
        if not self.active:
            self.update('waiting' if value else 'disabled')

    async def shutdown(self):
        self.server.close()
        self.refresh_task.cancel()
        await asyncio.gather(self.refresh_task,return_exceptions=True)
        for writer in tuple(self.writers):
            writer.transport.abort()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks),return_exceptions=True)
        # Python 3.12 waits for client transports too; close them before waiting.
        await self.server.wait_closed()

    def close(self):
        if not self.loop.is_closed():
            self.submit(self.shutdown()).result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
            self.loop.close()
