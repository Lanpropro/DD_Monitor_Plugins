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


def check(app, host, module=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
    capture = import_module('diagnose_match_ui_dispatch')
    from PySide6.QtCore import QEvent, QObject, QTimer
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
        encoded=json.dumps(rows)
        assert 'PRIVATE_VALUE' not in encoded and 'locals' not in encoded and 'objectName' not in encoded
        fake=Path(directory)/'host';fake.mkdir();(fake/'run.cmd').write_text('@echo off\n')
        launcher=capture.prepare(fake,Path(directory)/'output')
        assert 'call "'+str(fake/'run.cmd')+'"' in launcher.read_text(encoding='utf8')
        boot=(launcher.parent/'sitecustomize.py').read_text(encoding='utf8')
        assert 'diagnose_drag_windows.py' in boot and 'DDM_MATCH_UI_DISPATCH_CAPTURE' in boot
        compile(boot,'sitecustomize.py','exec')
        # The opt-in application subclass must be present in root integration.
        root_trace=getattr(app,'_match_ui_dispatch_trace',None)
        if root_trace is not None:
            root_trace.arm()
            live=QObject()
            assert app.sendEvent(live,QEvent(QEvent.User)) in (True,False)
            root_trace.finish()
            assert 'QObject:User' in root_trace.stats
        return dict(passed=True,return_exception_preserved=True,deleted_qt_objects_safe=True,
                    nested_spans=True,modal_wait_labelled=True,main_thread_only=True,
                    no_arguments_or_text=True,writer_stopped=True,launcher_opt_in=True,
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
