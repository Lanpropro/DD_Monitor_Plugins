"""Same-history starvation regression; descriptor histories, no media or accounts."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check(app, host, module):
    from ddm import plugins
    class History:
        start, end = 100., 130.
        def bounds(self):return self.start, self.end
        def bounds_at(self, _position, minimum_duration=0):
            return self.bounds() if self.end-self.start >= minimum_duration else None
    host.rooms, host.settings = [], {}
    with tempfile.TemporaryDirectory() as directory:
        viewer=module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host,plugins_dir=directory),'rebuffer_check'),{})
        try:
            viewer.add_room('1');viewer.add_room('2')
            main=viewer.rows['1'];history=History()
            main.decoder=SimpleNamespace(history=history,last_frame_received=100.,fps=60.)
            main.was_isolated=True
            viewer.alignment.automatic=False
            viewer.alignment.manual_locked=True
            viewer.alignment.lags={'1':0.,'2':-2.5}
            viewer.stable_reference_shift=0.
            viewer.safety_buffer.target=6.
            shifts={'1':0.,'2':-2.5}
            before=dict(viewer.alignment.lags)
            with patch.object(module.time,'monotonic',return_value=100.):
                held=viewer._recover_clock(129.96,shifts)
                assert viewer.sync_waiting and abs(held-129.95)<1e-8
                for available in (.35,.5,1.,3.,5.9):
                    history.end=held+available
                    assert viewer._recover_clock(held+1/60,shifts)==held
                    assert viewer.sync_waiting, 'Do not resume on 300ms and immediately starve again'
                history.end=held+6.1
                resumed=viewer._recover_clock(held+1/60,shifts)
                assert not viewer.sync_waiting and abs(resumed-held-1/60)<1e-8
                assert viewer.alignment.lags==before and viewer.alignment.manual_locked
                assert main.decoder.history is history
                assert not viewer.rows['2'].awaiting_confirmation
                # A cache smaller than the target must still be able to resume.
                viewer.sync_waiting=True;viewer.locked_clock=held
                history.start,history.end=held-.05,held+.45
                assert viewer._recover_clock(held+1/60,shifts)>held and not viewer.sync_waiting
                main.paused=True
                assert viewer._recover_clock(500.,shifts)==500.
            return dict(passed=True,media_playback=False,safety_reserve_before_resume=True,
                        small_capacity_resumes=True,manual_offsets_preserved=True,
                        same_history_preserved=True,pause_preserved=True)
        finally:
            main.decoder=None
            viewer.close();viewer.deleteLater();app.processEvents()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',type=Path,required=True)
    args=parser.parse_args()
    sys.path[:0]=[str(args.host.resolve()),str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE']='1'
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QWidget
    from plugins.match_sync import viewer as module
    app=QApplication([])
    print(check(app,QWidget(),module))


if __name__=='__main__':main()
