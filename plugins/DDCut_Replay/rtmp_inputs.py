"""GUI-owned RTMP definitions and archive indexing; workers publish snapshots."""
from copy import deepcopy
import queue
import secrets
import uuid
from pathlib import Path

from .rtmp_capture import RtmpReceiver


class RtmpInputs:
    def __init__(self, service, hub):
        self.service, self.hub = service, hub
        self.definitions = deepcopy(hub.preferences.get('rtmp_inputs',[]))
        self.receivers, self.errors, self.indexed = {}, {}, {}
        self.events = queue.Queue()
        self.covers = {}
        self.ffmpeg = ''
        for definition in self.definitions:
            try:
                self.start(definition)
            except Exception as error:
                self.errors[definition['id']] = str(error)

    def save(self):
        self.hub.preferences['rtmp_inputs'] = deepcopy(self.definitions)
        self.hub.save()

    def start(self, definition):
        options = self.service.options()
        if not str(options.get('directory') or '').strip():
            raise ValueError('请先设置录像保存目录')
        receiver = RtmpReceiver(definition,options,self.events)
        self.ffmpeg = options['ffmpeg']
        definition['port'] = receiver.definition['port']
        self.receivers[definition['id']] = receiver
        self.errors.pop(definition['id'],None)

    def add(self, name, *, port=0, lan=False):
        name = name.strip()
        if not name or len(name)>80 or not 0<=port<=65535:
            raise ValueError('请填写不超过80字的房间名称和有效端口')
        definition = dict(id=uuid.uuid4().hex,name=name,port=port,host='0.0.0.0' if lan else '127.0.0.1',
                          key=secrets.token_hex(16),enabled=True)
        self.start(definition)
        self.definitions.append(definition)
        self.save()
        return definition

    def set_enabled(self, identity, value):
        definition = next(row for row in self.definitions if row['id']==identity)
        if identity not in self.receivers:
            self.start(definition)
        self.receivers[identity].submit(self.receivers[identity].enable(value)).result(timeout=5)
        definition['enabled'] = bool(value)
        self.save()

    def remove(self, identity):
        receiver = self.receivers.pop(identity,None)
        if receiver:
            receiver.close()
        self.poll()
        self.definitions[:] = [row for row in self.definitions if row['id']!=identity]
        self.errors.pop(identity,None)
        self.save()

    def poll(self):
        infos = []
        while True:
            try:
                infos.append(self.events.get_nowait())
            except queue.Empty:
                break
        for receiver in self.receivers.values():
            info = receiver.snapshot()['info']
            if info:
                infos.append(info)
        for info in infos:
            cover = Path(info['path']).with_name('cover.png')
            if cover.is_file():
                info['cover_path'] = str(cover)
            elif info['path'] not in self.covers and not self.hub.closed:
                from .hls import input_options
                from .media import thumbnail
                # Options were taken on the GUI thread at receiver creation.
                ffmpeg = self.ffmpeg
                def make_cover(job, path=info['path'], target=cover, ffmpeg=ffmpeg):
                    result = thumbnail(job,ffmpeg,path,target.parent,input_args=input_options(path))
                    Path(result).replace(target)
                    return str(target)
                self.covers[info['path']] = self.hub.submit('RTMP封面：'+info['title'],'rtmp_cover',make_cover)
            signature = (info['duration'],info.get('ended_at'))
            existing = next((row for row in self.hub.entries if row['path']==info['path']),{})
            if self.indexed.get(info['path']) != signature or (info.get('cover_path') and not existing.get('cover_path')):
                self.indexed[info['path']] = signature
                self.hub.add_entry(info)

    def rows(self):
        return [(definition, self.receivers[definition['id']].snapshot() if definition['id'] in self.receivers
                 else dict(state='error',error=self.errors.get(definition['id'],''),info=None)) for definition in self.definitions]

    def sources(self):
        return [dict(source_id='rtmp:'+definition['id'],session_id=state['info']['session_id'],
                    state='recording',room=state['info']['room'],created_at=state['info']['created_at'])
                for definition,state in self.rows() if state['info'] and state['state']=='recording']

    def live_recording(self, identity, session):
        receiver = self.receivers.get(identity.removeprefix('rtmp:'))
        info = receiver.snapshot()['info'] if receiver else None
        if not info or info['session_id']!=session:
            raise RuntimeError('推流尚未封口首段或本次录制已结束')
        cover = Path(info['path']).with_name('cover.png')
        return {**info,**({'cover_path':str(cover)} if cover.is_file() else {})}

    def close(self):
        for receiver in self.receivers.values():
            receiver.close()
        self.receivers.clear()
        self.poll()


class InputRecordingService:
    def __init__(self, original, inputs):
        self.original, self.inputs = original, inputs

    def __getattr__(self, name):
        return getattr(self.original,name)

    def sources(self):
        return self.original.sources()+self.inputs.sources()

    def live_recording(self, identity, session):
        if identity.startswith('rtmp:'):
            return self.inputs.live_recording(identity,session)
        callback = getattr(self.original,'live_recording',None)
        if callback is None:
            raise RuntimeError('本体尚未提供连续回看，请更新配套本体')
        return callback(identity,session)

    def control(self, identity, action, *, session_id):
        if identity.startswith('rtmp:'):
            raise RuntimeError('请在“RTMP输入”页启用或停用推流；每次推流自动保存切片格式')
        return self.original.control(identity,action,session_id=session_id)

    def close(self):
        self.inputs.close()
        self.original.close()
