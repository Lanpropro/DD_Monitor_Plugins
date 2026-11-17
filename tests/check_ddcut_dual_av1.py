"""Real AV1 dual-output capture, separate folder, disabled chat and catalog scan."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

parser=argparse.ArgumentParser()
parser.add_argument('--host',type=Path,required=True)
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).parent.name=='tests' else Path('F:/CodexAppManager/Code/DD_Monitor_Plugins')
sys.path.insert(0,str(args.host.resolve()))
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['DDM_NO_SAVE']='1'
from PySide6.QtWidgets import QApplication
from ddm import recording, images
from ddm.recording_service import RecordingService
app=QApplication([])
spec=importlib.util.spec_from_file_location('ddcut_av1_dual',ROOT/'plugins/DDCut_Replay/plugin.py')
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
from ddcut_av1_dual.dual_recorder import configured_backends
from ddcut_av1_dual.recording_settings import scan_recording_directories
from ddcut_av1_dual.media import Job, probe
ffmpeg=shutil.which('ffmpeg')

def until(predicate):
    deadline=time.monotonic()+16
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for AV1 dual capture')

with tempfile.TemporaryDirectory(prefix='ddcut-dual-av1-') as temporary:
    root=Path(temporary)
    source=root/'source-av1.mp4'
    subprocess.run([ffmpeg,'-y','-hide_banner','-loglevel','error','-f','lavfi','-i',
        'color=blue:size=160x90:rate=10','-t','8','-c:v','libaom-av1','-cpu-used','8',
        '-threads','2','-g','10',str(source)],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    class Tile:
        room={'room_id':'offline','uname':'AV1 fixture','live':True}
        stream_url=str(source)
        stream_headers={}
        stream_codec='av1'
        def isVisible(self):
            return True
    tile=Tile()
    settings=dict(recording_dir=str(root/'native'),recording_codec='h264',recording_encoder='cpu',
        recording_format='mp4',recording_fps=10,recording_min_free_mb=256,recording_replay_enabled=True)
    recorder=recording.RecordingManager(settings)
    host=SimpleNamespace(recorder=recorder,capture_control=SimpleNamespace(),wall=SimpleNamespace(tiles=[tile]))
    service=RecordingService(SimpleNamespace(window=host,_active=True))
    clip,dual=configured_backends(dict(directory=str(root/'clips'),segment_seconds=1,danmaku=False))
    service.register_backend(clip,enabled=False)
    service.select_backend(dual)
    original_input=recording.input_args
    with patch.object(images,'REPO',str(root)),patch.object(recording,'input_args',
            side_effect=lambda url,headers:['-re',*original_input(url,headers)]):
        try:
            assert recorder.start(tile,recording=True)
            session=recorder.sessions[tile]
            until(lambda: session.review_info() is not None)
            service.record_danmaku('offline',{'text':'must not be saved'})
            info=session.review_info()
            assert info['format']=='fmp4' and info['video_codec']=='av1'
            assert all(Path(row['init']).is_file() for row in info['segments'])
            snapshot=service.snapshot(str(id(tile)),session.session_id)
            assert all(Path(path).is_relative_to(root/'clips') for path in snapshot['parts'])
            service.release(snapshot['lease'])
            recorder.stop(tile)
            until(lambda: not recorder.sessions and not recorder.exports)
            native,=list((root/'native').glob('*.mp4'))
            assert probe(Job('probe','native'),ffmpeg,native,root/'work')['duration']>0
            assert Path(info['danmaku_path']).read_text(encoding='utf-8')==''
            assert json.loads(Path(info['path']).with_name('recording.json').read_text(encoding='utf-8'))['complete']
            def codec(path):
                result=json.loads(subprocess.check_output([shutil.which('ffprobe'),'-v','error','-select_streams','v:0',
                    '-show_entries','stream=codec_name','-of','json',str(path)]))
                return result['streams'][0]['codec_name']
            assert codec(native)=='h264' and codec(info['path'])=='av1'
            rows=scan_recording_directories(Job('scan','both'),[str(root/'native'),str(root/'clips'),str(root/'clips')])
            assert len(rows)==3 and len({row['path'] for row in rows})==3
            # Clip-only can use its own folder when the native folder is blank.
            service.select_backend(clip)
            settings['recording_dir']=''
            assert recorder.directory_setting==str(root/'clips')
            assert recorder.start(tile,recording=True)
            solo=recorder.sessions[tile]
            until(lambda: solo.review_info() is not None)
            recorder.stop(tile)
            until(lambda: not recorder.sessions)
        finally:
            recorder.shutdown()
            service.close()
print('PASS: AV1/fMP4 dual raw copy + native H264, chat opt-out, separate directories, deduplicated scan, clip-only independent directory')
