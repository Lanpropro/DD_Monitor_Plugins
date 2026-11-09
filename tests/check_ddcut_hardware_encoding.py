"""Actual FFmpeg encoder probes and outputs; simulated failures test fallback only."""
from copy import deepcopy
import importlib
import unittest
from unittest.mock import patch

import check_ddcut_projects as fixtures
from check_ddcut_projects import app, project, engine, media, ffmpeg
from check_ddcut_realtime import RealtimeTests
from PySide6.QtCore import Qt, QTimer

encoding = importlib.import_module('ddcut_ranges_test.encoding')
hardware = importlib.import_module('ddcut_ranges_test.hardware_encoding')
chat = importlib.import_module('ddcut_ranges_test.chat')


class HardwareTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.ProjectTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.ProjectTests.tearDownClass.__func__)
    document = fixtures.ProjectTests.document
    editor = RealtimeTests.editor

    def test_profiles_old_settings_and_bitrate_overrides_quality(self):
        self.assertEqual(encoding.normalize({'codec':'h265'})['backend'],'software')
        with self.assertRaises(ValueError):
            encoding.normalize({'backend':'invalid'})
        for codec,prefix in [('h264','h264'),('h265','hevc')]:
            for backend in ('nvenc','qsv','amf','videotoolbox'):
                name=prefix+'_'+backend
                for priority in ('speed','quality'):
                    options=encoding.video_args(dict(codec=codec,priority=priority,bitrate=1800),name)
                    self.assertEqual(options[1],name)
                    self.assertIn('1800k',options)
                    self.assertFalse(set(('-cq','-crf','-global_quality','-qp_i','-qp_p','-x265-params')) & set(options))
                    self.assertEqual('hvc1' in options,codec=='h265')
        self.assertIn('-x265-params',encoding.video_args({'codec':'h265'}))

    def test_unusable_advertised_hardware_falls_back_cache_and_cancel(self):
        hardware._cache.clear()
        calls=[]
        def unavailable(job,command,*args,**kwargs):
            calls.append(command)
            if '-encoders' in command:
                return ' V..... h264_nvenc NVIDIA\n V..... h264_qsv Intel'
            raise RuntimeError('No device')
        job=media.Job('fallback','test')
        value=dict(backend='auto')
        with patch.object(hardware,'run_process',side_effect=unavailable):
            self.assertEqual(hardware.select_video_args(job,ffmpeg,value,self.root/'work',320,180)[1],'libx264')
            self.assertEqual(hardware.select_video_args(job,ffmpeg,value,self.root/'work',320,180)[1],'libx264')
        self.assertEqual(len(calls),3)
        self.assertIn('硬件不可用',job.message)
        job.cancel.set()
        with self.assertRaises(media.Cancelled):
            hardware.select_video_args(job,ffmpeg,value,self.root/'work',320,180)
        hardware._cache.clear()
        with patch.object(hardware,'run_process',side_effect=media.Cancelled):
            with self.assertRaises(media.Cancelled):
                hardware.select_video_args(media.Job('cancel','test'),ffmpeg,value,self.root/'work',320,180)
        self.assertFalse(hardware._cache)

    def test_actual_auto_clip_burn_merge_project_h264_and_h265(self):
        hardware._cache.clear()
        source=deepcopy(self.media_info[str(self.red)])
        log=self.root/'auto-chat.json'
        chat.write_json(log,[dict(time=.1,text='硬件编码测试')])
        source['danmaku_path']=str(log)
        selected={}
        for codec,expected in [('h264','h264'),('h265','hevc')]:
            settings=dict(codec=codec,backend='auto',priority='speed',bitrate=900)
            options=hardware.select_video_args(media.Job('actual probe','test'),ffmpeg,settings,self.root/'work',320,180)
            selected[codec]=options[1]
            for burn in (False,True):
                target=self.root/f'auto-{codec}-{burn}.mp4'
                result=media.export_clip(media.Job('auto clip','export'),ffmpeg,source,0,.5,'precise',target,self.root/'work',burn,settings)
                self.assertEqual(result['encoder'],options[1])
                info=media.probe(media.Job('probe','test'),ffmpeg,target,self.root/'work')
                self.assertEqual(info['video_codec'],expected)
                self.assertAlmostEqual(info['duration'],.5,delta=.1)
            target=self.root/f'auto-merge-{codec}.mp4'
            result=media.export_ranges(media.Job('auto merge','export'),ffmpeg,source,
                [dict(start=0,end=.3),dict(start=.5,end=.8)],'precise',[target],self.root/'work',True,False,settings)
            self.assertEqual(result['outputs'][0]['encoder'],options[1])
            self.assertAlmostEqual(result['outputs'][0]['actual_duration'],.6,delta=.12)
            data,_,_=self.document()
            data['clips']=data['clips'][:1]
            data['clips'][0].update(start=0,end=.5)
            data['encoding']=settings
            project.add_layer(data,'text',0,text='工程硬件导出')
            target=self.root/f'auto-project-{codec}.mp4'
            result=engine.render_project(media.Job('auto project','export'),ffmpeg,data,target,self.root/'work')
            self.assertEqual(result['encoder'],options[1])
            self.assertEqual(media.probe(media.Job('probe','test'),ffmpeg,target,self.root/'work')['video_codec'],expected)
        print('\nActual selected encoders:',selected,flush=True)

    def test_qt_controls_persistence_history_and_task_encoder_details(self):
        data,_,_=self.document()
        with self.editor(data,'hardware-ui') as (editor,view,host,hub):
            self.assertEqual(editor.encoding.value()['backend'],'software')
            revision=editor.document.revision
            editor.encoding.backend.setCurrentIndex(editor.encoding.backend.findData('auto'))
            self.assertEqual(editor.document.revision,revision+1)
            self.assertEqual(project.read_project(editor.document.path)['encoding']['backend'],'auto')
            editor.history(False)
            self.assertEqual(editor.encoding.value()['backend'],'software')
            editor.history(True)
            self.assertEqual(editor.encoding.value()['backend'],'auto')
            host.resize(920,600)
            app.processEvents()
            self.assertTrue(editor.encoding.backend.isVisible())
            job=media.Job('encoder history','export',state='done',result={'encoder':'h264_nvenc','output':'sample.mp4'})
            hub.jobs.append(job)
            view.refresh_tasks()
            row=next(view.tasks.topLevelItem(index) for index in range(view.tasks.topLevelItemCount())
                if view.tasks.topLevelItem(index).data(0,Qt.UserRole)==job.id)
            self.assertIn('h264_nvenc',row.toolTip(3))
            hub.save()
            self.assertIn('h264_nvenc',hub.state_file.read_text(encoding='utf-8'))


if __name__=='__main__':
    results=[]
    def checks():
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(HardwareTests)
        result=unittest.TextTestRunner().run(suite)
        results.append(result.wasSuccessful())
        app.quit()
    app.setQuitOnLastWindowClosed(False)
    QTimer.singleShot(0,checks)
    app.exec()
    app.shutdown()
    raise SystemExit(0 if results and results[0] else 1)
