"""Actual crop-only child, snapshot pixels, IPC result and process cleanup; offline."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from types import SimpleNamespace


BOOTSTRAP = '''import os,sys,json,time
from pathlib import Path
if sys.argv[1:2] == ['--plugin-worker']:
    from PySide6.QtCore import Qt,QTimer,QRectF
    from PySide6.QtWidgets import QWidget,QDialog,QDialogButtonBox
    def network_guard(event,args):
        if event in ('socket.connect','socket.getaddrinfo'):
            raise AssertionError('Crop worker network forbidden')
    sys.addaudithook(network_guard)
    original = QWidget.show
    def show(self):
        self.setAttribute(Qt.WA_DontShowOnScreen)
        result = original(self)
        if isinstance(self,QDialog) and not getattr(self,'_test_scheduled',False):
            self._test_scheduled=True
            action=os.environ['DDM_CROP_TEST_ACTION']
            def operate():
                if action=='crash':os._exit(7)
                if action=='hang':
                    while True:time.sleep(1)
                if action=='hold':return
                image=self.canvas.image
                info=dict(pid=os.getpid(),size=[image.width(),image.height()],
                          first=list(image.pixelColor(1,1).getRgb()),
                          last=list(image.pixelColor(image.width()-1,image.height()-1).getRgb()),
                          config_loaded='ddm.app' in sys.modules,
                          native_caption=self._native_caption,
                          caption_timer_active=self._caption_move_timer.isActive())
                Path(os.environ['DDM_CROP_TEST_RESULT']).write_text(json.dumps(info))
                if action=='accept':
                    # Block only this child UI to verify parent event-loop independence.
                    time.sleep(.25)
                    self.canvas.crop=QRectF(.2,.3,.4,.5)
                    self.buttons.button(QDialogButtonBox.Ok).click()
                else:self.reject()
            QTimer.singleShot(200,operate)
        return result
    QWidget.show=show
'''


def check(app, host, module):
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QProcess, QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    from shiboken6 import isValid
    from ddm import plugins as api
    control = import_module(module.__package__ + '.crop_process')
    protocol = import_module(module.__package__ + '.crop_protocol')
    assert hasattr(api, 'worker_command'), 'Host worker entry must be integrated'
    for invalid in (None, [0,0,0,1], [0,0,float('nan'),1], [0,0,2,1], ['x',0,1,1]):
        assert not protocol.valid_crop(invalid)
    assert protocol.valid_crop([.2,.3,.4,.5])
    image = QImage(640, 360, QImage.Format_RGB32)
    image.fill(QColor('red'))
    painter = QPainter(image)
    painter.fillRect(320,180,320,180,QColor('lime'))
    painter.end()
    snapshot = dict(title='Crop process fixture',crop=[0,0,1,1],floating=False,
                    selection_ready=False,position=[100,100],theme='dark',stylesheet='')
    outcomes = {}
    with tempfile.TemporaryDirectory() as directory:
        here = Path(directory)
        (here/'sitecustomize.py').write_text(BOOTSTRAP,encoding='utf-8')
        for action in ('accept','cancel','hold','owner_delete','crash','hang'):
            result_file = here/(action+'.json')
            env = dict(DDM_CROP_TEST_ACTION='hold' if action=='owner_delete' else action,DDM_CROP_TEST_RESULT=str(result_file),
                       PYTHONPATH=str(here)+os.pathsep+os.environ.get('PYTHONPATH',''))
            owner = QObject(host)
            request = control.CropProcess(owner,snapshot)
            results, failures, pids, launcher_pids, ticks, ready_ticks = [], [], [], [], [], []
            heartbeat = QTimer(host)
            heartbeat.setInterval(10)
            heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
            request.finished.connect(results.append)
            request.failed.connect(failures.append)
            def ready():
                pids.append(request.worker_pid)
                launcher_pids.append(request.process.processId())
                ready_ticks.append(len(ticks))
                if action=='cancel':request.set_frame((42.5,image))
                elif action=='hold':request.reject()
                elif action=='owner_delete':owner.deleteLater()
                elif action=='hang':QTimer.singleShot(350,request.reject)
            request.ready.connect(ready)
            if action!='cancel':request.set_frame((42.5,image))
            process = request.process
            exit_codes = []
            process.finished.connect(lambda code,*_args: exit_codes.append(code))
            heartbeat.start()
            try:
                with patch.dict(os.environ,env):
                    request.show()
                deadline = time.monotonic()+12
                while (not results or not exit_codes) and time.monotonic()<deadline:
                    QTest.qWait(10)
                assert results and exit_codes, (action,failures,process.state())
                assert pids and pids[0]!=os.getpid(), (action,pids)
                assert request.shared is None
                assert request._worker_handle is None
                if action=='accept':
                    assert results==[QDialog.Accepted] and not failures, failures
                    assert request.crop==QRectF(.2,.3,.4,.5)
                    assert request.frame[0]==42.5 and request.frame[1] is image
                    assert len(ticks)-ready_ticks[0]>=5, 'Child UI delay must not block the parent loop'
                else:
                    assert results==[QDialog.Rejected]
                    assert bool(failures)==(action=='crash'), failures
                if action in ('accept','cancel'):
                    report=json.loads(result_file.read_text())
                    assert report['size']==[640,360] and report['first']==[255,0,0,255]
                    assert report['last']==[0,255,0,255] and not report['config_loaded']
                    assert report['native_caption'] and not report['caption_timer_active']
                outcomes[action]=dict(result=results[0],exit_code=exit_codes[0],worker_pid=pids[0],launcher_pid=launcher_pids[0])
                QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
                assert not isValid(request), 'Supervisor must be released after child exit'
            finally:
                heartbeat.stop()
                heartbeat.deleteLater()
                if isValid(process) and process.state()!=QProcess.NotRunning:
                    process.kill()
                    QTest.qWait(100)
                if isValid(owner):owner.deleteLater()
        # Exercise the real Viewer callback and its stale-source gate with
        # actual child results, rather than only the process supervisor.
        host.settings, host.rooms = {}, []
        manager = api.PluginManager(window=host,plugins_dir=str(here/'empty-plugins'))
        for mode in ('apply','stale','remove'):
            viewer=module.Viewer(api.PluginContext(manager,'crop_process_'+mode),{})
            viewer.add_room('1')
            row=viewer.rows['1']
            row.decoder=SimpleNamespace(history=object(),stop=lambda:None)
            viewer.canvas.set_frame((42.5,image))
            results, exits = [], []
            env=dict(DDM_CROP_TEST_ACTION='hold' if mode=='remove' else 'accept',
                     DDM_CROP_TEST_RESULT=str(here/(mode+'.json')),
                     PYTHONPATH=str(here)+os.pathsep+os.environ.get('PYTHONPATH',''))
            try:
                with patch.dict(os.environ,env), patch.object(viewer,'show_floating_crop') as applied:
                    viewer.choose_crop(row,floating=True)
                    request=viewer.crop_dialog
                    assert isinstance(request,control.CropProcess)
                    request.finished.connect(results.append)
                    request.process.finished.connect(lambda code,*_args:exits.append(code))
                    if mode=='stale':
                        request.ready.connect(lambda:setattr(row.decoder,'history',object()))
                    elif mode=='remove':
                        request.ready.connect(lambda:viewer.remove_room('1'))
                    deadline=time.monotonic()+12
                    while (not results or not exits) and time.monotonic()<deadline:
                        QTest.qWait(10)
                    assert results and exits and viewer.crop_dialog is None, (mode,results,exits)
                    if mode=='apply':
                        applied.assert_called_once_with(row,(.2,.3,.4,.5),(42.5,image))
                    else:
                        applied.assert_not_called()
            finally:
                if '1' in viewer.rows:viewer.rows['1'].decoder=None
                viewer.close();viewer.deleteLater()
                app.processEvents()
        # Keep the previous in-process UI available to hosts without the new API.
        with patch.object(control,'api',SimpleNamespace()):
            fallback=control.create_picker(host,snapshot)
            assert isinstance(fallback,control.CropPicker)
            assert not fallback._native_caption, 'Older hosts must retain Qt-coalesced caption movement'
            fallback.reject()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        # Cancellation before startup must never launch a child.
        request=control.CropProcess(host,snapshot)
        request.reject()
        request.show()
        assert request.process.state()==QProcess.NotRunning
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    return dict(passed=True,actual_child_processes=outcomes,shared_snapshot_pixels=True,
                deferred_frame=True,parent_event_loop_independent=True,
                cancel_crash_and_prestart_cleanup=True,real_streams=0,real_messages_sent=0,
                hung_real_worker_terminated=True,
                viewer_result_and_stale_source_gate=True,older_host_compatibility=True,
                isolated_worker_native_caption=True,
                live_performance=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',type=Path,required=True)
    args=parser.parse_args()
    sys.path[:0]=[str(args.host),str(Path(__file__).resolve().parents[1])]
    from PySide6.QtWidgets import QApplication,QWidget
    from plugins.match_sync import viewer as module
    app=QApplication([])
    print(check(app,QWidget(),module))


if __name__=='__main__':main()
