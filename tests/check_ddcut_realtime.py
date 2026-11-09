"""Real PCM/video frames, native live gestures and audio-clock continuity."""
from array import array
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import importlib
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import check_ddcut_projects as fixtures
from check_ddcut_projects import app, args, remainder, engine, media, project, run, ffmpeg, ui, until
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMainWindow

audio = importlib.import_module('ddcut_ranges_test.project_audio')
geometry = importlib.import_module('ddcut_ranges_test.graphic_geometry').geometry
hls = importlib.import_module('ddcut_ranges_test.hls')


class RealtimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ProjectTests.setUpClass.__func__(cls)
        cls.sequence = cls.root/'sequence.mp4'
        run(ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:size=320x180:rate=20:duration=1',
            '-f', 'lavfi', '-i', 'color=blue:size=320x180:rate=20:duration=1',
            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map','[v]',
            '-c:v','libx264','-threads','1','-g','20',cls.sequence)
        cls.media_info[str(cls.sequence)] = engine.inspect_file(media.Job('sequence','test'),ffmpeg,cls.sequence,cls.root/'work')

    tearDownClass = classmethod(fixtures.ProjectTests.tearDownClass.__func__)

    def document(self):
        data = project.new_project('实时预览')
        data['media'] = deepcopy(self.media_info)
        data['render'] = dict(width=320,height=180,fps=20)
        project.append_clip(data,str(self.sequence),0,1)
        project.append_clip(data,str(self.sequence),1,2)
        return data

    @contextmanager
    def editor(self,data,name):
        hub = media.TaskHub(self.root/name)
        host = QMainWindow()
        ui.theme.apply('dark')
        host.setStyleSheet(ui.theme.qss())
        host.setAttribute(Qt.WA_ShowWithoutActivating if args.native else Qt.WA_DontShowOnScreen)
        if args.native:
            host.move(-30000,-30000)
        owner = SimpleNamespace(context=SimpleNamespace(window=host),hub=hub,
            service=SimpleNamespace(sources=lambda:[],options=lambda:{'ffmpeg':ffmpeg}),
            preview_server=SimpleNamespace(source_url=lambda path:path))
        def headless(view,path):
            from ddm.player import EMBEDDED_OPTIONS
            import mpv
            view.stop()
            view.core = mpv.MPV(**EMBEDDED_OPTIONS,vo='null',ao='null',pause=True,keep_open=True)
            view.core.play(str(path))
        with nullcontext() if args.native else patch.object(ui.Preview,'load',headless):
            view = ui.Workspace(owner)
            host.setCentralWidget(view)
            host.resize(1440,900)
            host.show()
            doc = project.ProjectDocument(hub.root/'projects/live.ddcut.json',data)
            doc.save()
            view.open_project_document(doc)
            try:
                until(lambda:not view.project_editor.preview_dirty or
                      (view.project_editor.runtime and view.project_editor.runtime.error),timeout=30)
                self.assertFalse(view.project_editor.runtime.error)
                yield view.project_editor,view,host,hub
            finally:
                view.close()
                hub.close()
                host.close()
                host.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
                app.processEvents()

    def color(self,editor,x,y):
        image=editor.runtime.last_frame
        return image.pixelColor(min(image.width()-1,round(x*image.width())),min(image.height()-1,round(y*image.height())))

    def test_pcm_mix_fades_speed_and_fractional_start_match_export(self):
        data,_,_=fixtures.ProjectTests.document(self)
        data['clips'][0].update(speed=1.5,volume=.7)
        identity=project.add_layer(data,'audio',.2,str(self.audio))
        data['layers'][-1].update(duration=1.2,volume=.4,fade_in=.2,fade_out=.3)
        project.add_layer(data,'video',.4,str(self.red))
        data['layers'][-1].update(duration=.8,volume=.2,**{'in':.5})
        begin=.45
        pcm=run(*audio.audio_command(ffmpeg,data,begin)).stdout
        self.assertAlmostEqual(len(pcm)/192000,project.duration(data)-begin,delta=.002)
        target=self.root/'audio-reference.mp4'
        engine.render_project(media.Job('audio reference','export'),ffmpeg,data,target,self.root/'work',begin=begin,length=1.5)
        encoded=run(ffmpeg,'-v','error','-i',target,'-vn','-ar','48000','-ac','2','-f','s16le','pipe:1').stdout
        def rms(content,start):
            samples=array('h')
            samples.frombytes(content[round(start*48000)*4:round((start+.08)*48000)*4])
            return math.sqrt(sum(value*value for value in samples)/len(samples))/32768
        for start in (.05,.4,1.1):
            expected,actual=rms(encoded,start),rms(pcm,start)
            self.assertAlmostEqual(actual,expected,delta=max(.01,expected*.15))
        copy=deepcopy(data)
        text=project.add_layer(copy,'text',0)
        self.assertEqual(audio.audio_signature(copy),audio.audio_signature(data))

    def test_native_seek_cut_live_graphics_pip_cancel_and_audio_clock_reuse(self):
        data=self.document()
        project.add_layer(data,'audio',0,str(self.audio))
        data['layers'][-1]['duration']=2
        image_id=project.add_layer(data,'image',0,str(self.image))
        data['layers'][-1].update(x=.1,y=.1,w=.2,h=.2,duration=2)
        pip=project.add_layer(data,'video',.2,str(self.sequence))
        data['layers'][-1].update(x=.1,y=.55,w=.2,h=.2,duration=.6,**{'in':1})
        with self.editor(data,'live-ui') as (editor,view,host,hub):
            runtime=editor.runtime
            editor.seek(.8)
            until(lambda:not editor.preview_dirty)
            clock=runtime.clock
            editor.toggle_play()
            until(lambda:editor.position>1.2)
            self.assertIs(runtime.clock,clock)
            until(lambda:self.color(editor,.8,.8).blue()>180)
            editor.toggle_play()
            created=[]
            initialize=audio.AudioClock.__init__
            def record_clock(instance,*args,**kwargs):
                created.append(1)
                initialize(instance,*args,**kwargs)
            with patch.object(audio.AudioClock,'__init__',record_clock):
                for position in (.2,1.3,.35):
                    editor.seek(position)
                until(lambda:not editor.preview_dirty)
            self.assertEqual(len(created),1)
            self.assertGreater(self.color(editor,.8,.8).red(),180)
            self.assertEqual(self.color(editor,.8,.8).alpha(),255)
            clock=runtime.clock
            editor.select_item(image_id)
            stage=editor.stage
            frame=stage.frame()
            center=geometry(editor.selected()[0],frame)[0].map(QPointF())
            target=QPointF(frame.left()+frame.width()*.65,frame.top()+frame.height()*.65)
            revision=editor.document.revision
            stage.pointer('down',center)
            stage.pointer('move',target)
            self.assertEqual(editor.document.revision,revision)
            self.assertGreater(self.color(editor,.65,.65).green(),180)
            self.assertGreater(self.color(editor,.2,.2).red(),180)
            stage.cancel()
            self.assertGreater(self.color(editor,.2,.2).green(),180)
            stage.pointer('down',center)
            stage.pointer('move',target)
            stage.pointer('up',target)
            until(lambda:not editor.preview_dirty)
            self.assertEqual(editor.document.revision,revision+1)
            self.assertIs(runtime.clock,clock)
            editor.select_item(pip)
            frame=stage.frame()
            center=geometry(editor.selected()[0],frame)[0].map(QPointF())
            target=QPointF(frame.left()+frame.width()*.65,frame.top()+frame.height()*.2)
            stage.pointer('down',center)
            stage.pointer('move',target)
            self.assertGreater(self.color(editor,.65,.2).blue(),180)
            self.assertGreater(self.color(editor,.2,.65).red(),180)
            stage.cancel()
            self.assertGreater(self.color(editor,.2,.65).blue(),180)
            self.assertIs(runtime.clock,clock)
            runtime.set_mute(False)
            self.assertFalse(runtime.clock.core.mute)
            runtime.set_mute(True)
            self.assertFalse(any(job.kind=='project_preview' for job in hub.jobs))
            self.assertFalse((hub.root/'project_previews').exists())
            if args.native and args.screenshots:
                args.screenshots.mkdir(parents=True,exist_ok=True)
                editor.preview.core.command('screenshot-to-file',str(args.screenshots/'live-preview.png'),'window')
            sources=list(runtime.sources.values())
            process=runtime.clock.pipe.process
            editor.shutdown_preview()
            self.assertTrue(runtime.closed)
            self.assertFalse(runtime.timer.isActive())
            self.assertTrue(all(source.core is None and source.context is None for source in sources))
            self.assertIsNotNone(process.poll())

    def test_hls_av1_frames_and_missing_source_recovery(self):
        folder=self.root/'av1-live'
        folder.mkdir()
        playlist=folder/'source.m3u8'
        run(ffmpeg,'-y','-v','error','-f','lavfi','-i','color=blue:size=160x90:rate=10',
            '-t','2','-c:v','libaom-av1','-cpu-used','8','-threads','2','-g','10',
            '-f','hls','-hls_time','1','-hls_list_size','0','-hls_segment_type','fmp4',
            '-hls_fmp4_init_filename',folder/'init.mp4','-hls_segment_filename',folder/'part-%03d.m4s',playlist)
        hls.write_playlist(playlist,hls.read_segments(playlist),complete=True)
        info=engine.inspect_file(media.Job('hls','test'),ffmpeg,playlist,self.root/'work')
        data=project.new_project('HLS AV1')
        data['render']=dict(width=320,height=180,fps=20)
        data['media'][str(playlist)]=info
        identity=project.append_clip(data,str(playlist))
        with self.editor(data,'hls-ui') as (editor,view,host,hub):
            self.assertGreater(self.color(editor,.5,.5).blue(),180)
            absent=str(self.root/'absent.mp4')
            def break_source(value):
                value['media'][absent]={**info,'path':absent}
                value['clips'][0]['source']=absent
            editor.change(break_source)
            until(lambda:bool(editor.runtime.error))
            self.assertFalse(editor.runtime.want_play)
            self.assertTrue(editor.runtime.clock.core.pause)
            editor.change(lambda value:value['clips'][0].update(source=str(playlist)))
            until(lambda:not editor.preview_dirty)
            self.assertFalse(editor.runtime.error)
            self.assertGreater(self.color(editor,.5,.5).blue(),180)

    def test_black_video_is_ready_after_decode_and_paused_seek_resize(self):
        source=self.root/'black-preview.mp4'
        run(ffmpeg,'-y','-v','error','-f','lavfi','-i','color=black:size=160x90:rate=10',
            '-t','2','-c:v','libx264','-threads','1',source)
        info=engine.inspect_file(media.Job('black','test'),ffmpeg,source,self.root/'work')
        data=project.new_project('黑色画面')
        data['render']=dict(width=320,height=180,fps=20)
        data['media'][str(source)]=info
        project.append_clip(data,str(source))
        with self.editor(data,'black-ui') as (editor,view,host,hub):
            self.assertEqual(self.color(editor,.5,.5).getRgb(),(0,0,0,255))
            editor.seek(.6)
            until(lambda:not editor.preview_dirty or editor.runtime.error)
            self.assertFalse(editor.runtime.error)
            self.assertAlmostEqual(editor.position,.6,delta=.05)
            host.resize(920,600)
            app.processEvents()
            until(lambda:editor.runtime.last_frame.width()<=960)
            self.assertEqual(self.color(editor,.5,.5).getRgb(),(0,0,0,255))


if __name__=='__main__':
    results=[]
    def checks():
        result=unittest.main(argv=[sys.argv[0],*remainder],exit=False)
        results.append(result.result.wasSuccessful())
        app.quit()
    app.setQuitOnLastWindowClosed(False)
    QTimer.singleShot(0,checks)
    app.exec()
    app.shutdown()
    raise SystemExit(0 if results and results[0] else 1)
