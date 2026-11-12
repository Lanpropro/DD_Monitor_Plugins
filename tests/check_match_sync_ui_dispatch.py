"""Dispatch-trace mechanics only; synthetic callbacks are not live performance evidence."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
    capture = import_module('diagnose_match_ui_dispatch')
    from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer, QTimerEvent
    from PySide6.QtWidgets import QDialog
    from shiboken6 import delete, isValid
    with tempfile.TemporaryDirectory() as directory:
        target=Path(directory)/'trace.jsonl'
        trace=capture.UiTrace(target)
        try:
            sentinel=object()
            assert trace.call('disabled',lambda:sentinel) is sentinel and not trace.stats
            trace.arm()
            def child():
                time.sleep(.012)
                return sentinel
            def parent():
                return trace.call('child',child)
            assert trace.call('parent',parent) is sentinel
            try:
                trace.call('failure',lambda:(_ for _ in ()).throw(ValueError('PRIVATE_VALUE')))
            except ValueError:
                pass
            else:
                raise AssertionError('The original exception was hidden')
            assert trace.stack==[]
            # Wake metadata is captured before delivery, even if Qt deletes both objects.
            class Viewer(QObject):pass
            viewer=Viewer()
            viewer.render_timer=QTimer(viewer)
            viewer.render_timer.setInterval(7)
            unrelated=QTimer(viewer)
            assert trace.dispatch(lambda receiver,event:sentinel,unrelated,QTimerEvent(1)) is sentinel
            for _ in range(2):
                tick=QTimerEvent(1)
                assert trace.dispatch(lambda receiver,event:sentinel,viewer.render_timer,tick) is sentinel
                delete(tick)
            tick=QTimerEvent(1)
            def destroy_timer(receiver,event):
                delete(receiver);delete(event)
                return sentinel
            assert trace.dispatch(destroy_timer,viewer.render_timer,tick) is sentinel
            class FloatingCrop:
                drag=None
            floating=FloatingCrop()
            for kind,drag in ((QEvent.MouseButtonPress,None),(QEvent.MouseMove,(None,None,None)),
                              (QEvent.MouseMove,(None,None,(1,-1))),
                              (QEvent.MouseButtonRelease,(None,None,(1,-1))),(QEvent.Wheel,None)):
                floating.drag=drag
                assert trace.dispatch(lambda receiver,event:sentinel,floating,QEvent(kind)) is sentinel
            class Canvas:
                paint_probe=object()
                frame_key=1.25
            assert trace.dispatch(lambda receiver,event:sentinel,Canvas(),QEvent(QEvent.Paint)) is sentinel
            receiver=QObject();event=QEvent(QEvent.User)
            def destroy(receiver,event):
                delete(receiver)
                delete(event)
                time.sleep(.01)
                return sentinel
            assert trace.dispatch(destroy,receiver,event) is sentinel
            assert not isValid(receiver) and not isValid(event)
            # Main-thread-only recording and Qt zero-argument slot arity.
            import threading
            background=threading.Thread(target=lambda:trace.call('background',lambda:None))
            background.start();background.join(1)
            assert 'background' not in trace.stats
            class Owner:
                def close(self):return sentinel
            trace.wrap(Owner,'close','owner.close',no_args=True)
            assert Owner().close() is sentinel
            assert Owner.close.__code__.co_argcount==1
            # Caption metadata is primitive; preserve actual handled returns.
            import ctypes
            from ctypes import wintypes
            class Caption:
                _caption_drag = None
                def mouseMoveEvent(self, event):return sentinel
                def nativeEvent(self, event_type, message):return (True, 17)
            trace.wrap_caption(Caption)
            caption=Caption()
            message=wintypes.MSG();message.message=0x00a1;message.wParam=2
            pointer=ctypes.addressof(message)
            assert caption.nativeEvent(b'PRIVATE_VALUE',pointer)==(True,17)
            assert caption.mouseMoveEvent(sentinel) is sentinel
            # After a native callback destroys its receiver, never inspect it again.
            class DeletedCaption:
                def __init__(self):self._caption_drag = None
                def mouseMoveEvent(self, event):return sentinel
                def nativeEvent(self, event_type, message):
                    del self._caption_drag
                    return (False,0)
            trace.wrap_caption(DeletedCaption)
            assert DeletedCaption().nativeEvent(b'PRIVATE_VALUE',pointer)==(False,0)
            trace.enabled=False
            assert caption.nativeEvent(None,None)==(True,17)
            trace.enabled=True
            # Real Qt nested delivery without media or network; finish remains explicit.
            dialog=QDialog(host)
            QTimer.singleShot(10,dialog.reject)
            assert trace.call('QDialog.exec',dialog.exec)==QDialog.Rejected
            dialog.deleteLater();app.processEvents()
            trace.finish()
        finally:
            trace.close()
        rows=[json.loads(line) for line in target.read_text(encoding='utf8').splitlines()]
        slow=[r for r in rows if r['phase']=='slow-span']
        assert any(r['label']=='QObject:User' and r['duration_ms']>=8 for r in slow)
        child_row=next(r for r in slow if r['label']=='child')
        parent_row=next(r for r in slow if r['label']=='parent')
        assert child_row['parent']==parent_row['span'] and parent_row['children']==1
        assert parent_row['exclusive_wall_ms']<parent_row['duration_ms']
        assert any(r['label']=='QDialog.exec' and r['modal_wait_possible'] for r in slow)
        assert rows[-1]['phase']=='capture-stop' and not trace.worker.is_alive()
        native=[r for r in rows if r['phase']=='caption-native']
        wakes=[r for r in rows if r['phase']=='render-wake']
        assert len(wakes)==3 and wakes[0]['gap_ms'] is None and wakes[1]['gap_ms'] is not None
        assert all(r['interval_ms']==7 for r in wakes) and wakes[1]['thread_cpu_gap_ms'] is not None
        inputs=[r for r in rows if r['phase']=='floating-input']
        assert [(r['dragging'],r['resizing']) for r in inputs]==[
            (False,False),(True,False),(True,True),(True,True),(False,False)]
        assert next(r for r in rows if r['phase']=='main-paint')['frame_key']==1.25
        if sys.platform=='win32':
            assert [r['point'] for r in native]==['enter','return','enter','return']
            assert native[1]['handled'] is True and native[3]['handled'] is False
        encoded=json.dumps(rows)
        assert 'PRIVATE_VALUE' not in encoded and 'locals' not in encoded and 'objectName' not in encoded
        fake=Path(directory)/'host';fake.mkdir();(fake/'run.cmd').write_text('@echo off\n')
        launcher=capture.prepare(fake,Path(directory)/'output')
        assert 'call "'+str(fake/'run.cmd')+'"' in launcher.read_text(encoding='utf8')
        boot=(launcher.parent/'sitecustomize.py').read_text(encoding='utf8')
        assert 'diagnose_drag_windows.py' in boot and 'DDM_MATCH_UI_DISPATCH_CAPTURE' in boot
        compile(boot,'sitecustomize.py','exec')
        focused=capture.UiTrace(Path(directory)/'focused.jsonl',playback_only=True)
        try:
            focused.arm()
            floating=FloatingCrop();floating.drag=(None,None,None)
            for _ in range(200):
                assert focused.dispatch(lambda receiver,event:sentinel,floating,QEvent(QEvent.MouseMove)) is sentinel
            assert not focused.stats and focused.mouse_moves==200
            focused.dispatch(lambda receiver,event:sentinel,floating,QEvent(QEvent.MouseButtonRelease))
            with patch.object(capture.time,'perf_counter',side_effect=(1.,1.05,2.,2.08)):
                focused.native_message(0x200);focused.native_message(0x113)
                focused.event_loop('about-to-block');focused.event_loop('awake')
            observer=capture.native_observer(focused)
            assert observer.nativeEventFilter(b'unknown',None)==(False,0)
            if sys.platform=='win32':
                message=wintypes.MSG();message.message=0x200;message.lParam=1234567
                assert observer.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(message))==(False,0)
                assert focused.native_counts['mouse_move']==2
            focused.finish()
        finally:
            focused.close()
        focus_rows=[json.loads(line) for line in focused.path.read_text(encoding='utf8').splitlines()]
        assert len([r for r in focus_rows if r['phase']=='floating-input'])==1
        assert next(r for r in focus_rows if r['phase']=='floating-input')['mouse_moves']==200
        assert next(r for r in focus_rows if r['phase']=='native-gap')['gap_ms']==50
        assert next(r for r in focus_rows if r['phase']=='event-loop-gap')['gap_ms']==80
        assert '1234567' not in json.dumps(focus_rows)
        launcher=capture.prepare(fake,Path(directory)/'focused-output',playback_only=True)
        assert 'playback_only=True' in (launcher.parent/'sitecustomize.py').read_text(encoding='utf8')
        # The opt-in application subclass must be present in root integration.
        root_trace=getattr(app,'_match_ui_dispatch_trace',None)
        if root_trace is not None:
            if module is not None:
                from PySide6.QtWidgets import QWidget
                if not root_trace.playback_only:
                    assert hasattr(module.floating_control.CropDialog.nativeEvent,'__wrapped__')
                    assert hasattr(module.floating_control.CropDialog.mouseMoveEvent,'__wrapped__')
                    assert hasattr(QWidget.move,'__wrapped__')
                else:
                    assert not hasattr(QWidget.move,'__wrapped__')
                    assert not hasattr(module.FloatingCrop.mouseMoveEvent,'__wrapped__')
                assert hasattr(module.FloatingCrop.fit_geometry,'__wrapped__')
                assert hasattr(module.Canvas.show_at,'__wrapped__')
                assert hasattr(module.Viewer.render,'__wrapped__')
            root_trace.arm()
            live=QObject()
            assert app.sendEvent(live,QEvent(QEvent.User)) in (True,False)
            assert ('QObject:User' in root_trace.stats) is not root_trace.playback_only
            # Actual QTimer deliveries through the root application, not a performance benchmark.
            timer_host=Viewer()
            timer_host.render_timer=QTimer(timer_host)
            timer_host.render_timer.setInterval(7)
            loop=QEventLoop()
            ticks=[]
            def timeout():
                ticks.append(True)
                if len(ticks)==3:
                    timer_host.render_timer.stop()
                    loop.quit()
            timer_host.render_timer.timeout.connect(timeout)
            guard=QTimer();guard.setSingleShot(True);guard.timeout.connect(loop.quit);guard.start(2000)
            timer_host.render_timer.start();loop.exec();guard.stop()
            assert len(ticks)==3 and timer_host.render_timer.interval()==7
            assert timer_host.render_timer in root_trace.wakes
            if root_trace.playback_only:
                assert root_trace.loop_counts and root_trace.native_counts
            root_trace.finish();root_trace.arm()
            assert not root_trace.wakes
            root_trace.finish()
        return dict(passed=True,return_exception_preserved=True,deleted_qt_objects_safe=True,
                    nested_spans=True,modal_wait_labelled=True,main_thread_only=True,
                    no_arguments_or_text=True,writer_stopped=True,launcher_opt_in=True,
                    render_wake_metadata=True,floating_input_boundaries=True,main_paint_metadata=True,
                    focused_mouse_aggregation=True,native_filter_passthrough=True,loop_boundaries=True,
                    root_dispatch_installed=root_trace is not None,live_performance=False)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.host))
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QWidget
    app=QApplication([])
    print(check(app,QWidget()))


if __name__=='__main__':main()
