"""Regression for one-click calibration, audio-only delay ramp and evidence expiry."""
import argparse
from pathlib import Path
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host.resolve()), str(Path(__file__).resolve().parents[1])]
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    from plugins.match_sync.audio import AudioPump
    from plugins.match_sync.panels import ConsoleIconButton
    from plugins.match_sync.sound_calibration import LagEvidence, SoundCalibration
    from plugins.match_sync.engine import RATE

    def result(lag):
        return dict(pairs=[dict(first=0, second=1, windows=[dict(usable=True, lag_ms=lag)] * 4)])

    app = QApplication([])
    v = QWidget()
    v.rows = {k: SimpleNamespace(decoder=SimpleNamespace(history=object()), paused=False,
                                 sync_state='已同步', audio_controls=(True, 0, 40), mix_gain=1.,
                                 calibrate_sound=ConsoleIconButton('sound_calibration', '一键校准比赛声', v),
                                 undo_sound_calibration=ConsoleIconButton('undo_sound_calibration', '撤销本路声音校准', v),
                                 sound_offset_info=QLabel(v),
                                 label=lambda: '测试副路') for k in ('main', 'second')}
    v.running, v.generation = True, 1
    v.main = SimpleNamespace(currentData=lambda: 'main')
    v.replay = SimpleNamespace(active=False, presentation=lambda clock, shifts: (clock, shifts))
    v.independent_playback, v.relative_rates = {}, {}
    v.shifts = lambda: {'main': 0., 'second': 2.}
    v.playback_rate, v.sync_waiting = 1., False
    v.audio = AudioPump(v)
    bar = SoundCalibration(v)

    for key, row in v.rows.items():
        row.sound_offset_info.hide()
        row.calibrate_sound.clicked.connect(lambda _checked, k=key: bar.calibrate(k))
        row.undo_sound_calibration.clicked.connect(lambda _checked, k=key: bar.restore(k))
    measure = v.rows['second'].calibrate_sound
    main_measure = v.rows['main'].calibrate_sound
    undo = v.rows['second'].undo_sound_calibration
    caption = v.rows['second'].sound_offset_info
    main_caption = v.rows['main'].sound_offset_info
    with patch('time.monotonic', return_value=100.) as now:
        bar.enable()
        assert bar.phase == 'sampling' and measure.isEnabled() and main_measure.isEnabled()
        assert '预采样' in bar.status_text
        assert caption.isHidden() and main_caption.isHidden() and '预采样' in measure.toolTip()
        measure.click()
        assert bar.phase == 'sampling' and bar.requested and not measure.isEnabled() and not main_measure.isEnabled()
        assert not caption.isHidden() and caption.text() == '等待比赛声…' and main_caption.isHidden()
        assert '等待共同比赛声' in bar.status_text

        def observations(lag):
            bar.evidence.add(result(lag), 0, now.return_value - 24)
            bar.evidence.add(result(lag), 0, now.return_value - 12)
            bar.poll()

        observations(-48.)
        assert v.audio.trim_goal('second') == 48 and v.audio.trim_goal('main') == 0
        assert not measure.isEnabled() and bar.estimate('second') is None
        assert '正在平滑调整' in bar.status_text and '延后 48 ms' in caption.text()
        now.return_value += 151
        bar.poll()
        assert bar.phase == 'sampling' and bar.probe is None and bar.until > now.return_value
        for _ in range(750):
            now.return_value += .01
            v.audio.update_snapshot()
        assert v.audio.trims_settled()
        bar.poll()
        assert '正在复测' in bar.status_text
        inputs = v.audio.snapshot[1]
        assert inputs[0][1] == 0. and abs(inputs[1][1] - 1.952) < .5 / RATE
        assert v.shifts()['second'] == 2.
        observations(2.)
        assert '复测通过' in caption.text()
        assert not undo.isHidden() and v.rows['main'].undo_sound_calibration.isHidden()
        undo.click()
        assert v.audio.trim_goal('second') == 0
        assert undo.isHidden() and caption.isHidden()
        for _ in range(750):
            now.return_value += .01
            v.audio.update_snapshot()
        assert not v.audio.sound_trims and v.audio.snapshot[1][1][1] == 2.
        observations(142.)
        assert bar.phase == 'ready' and v.audio.trim_goal('second') == 0
        main_measure.click()
        assert v.audio.trim_goal('second') == -142
        v.rows['main'].decoder.history = object()
        v.rows['second'].sync_state = '等待对齐'
        v.audio.update_snapshot()
        bar.poll()
        assert v.audio.trim_goal('second') == -142 and bar.phase == 'unavailable' and bar.timer.isActive()
        assert '补偿已保留，等待重新入混' in bar.status_text and '待入混' in caption.text()
        v.rows['second'].decoder.history = object()
        v.rows['second'].sync_state = '已同步'
        v.audio.update_snapshot()
        bar.poll()
        assert bar.phase == 'sampling' and bar.verifying and bar.estimate('second') is None and not undo.isHidden()
        assert '复测通过' not in caption.text()
        assert v.audio.trim_goal('second') == -142
        for _ in range(2200):
            now.return_value += .01
            v.audio.update_snapshot()
        bar.poll()
        observations(12.)
        assert bar.phase == 'residual' and '仍有偏差' in caption.text()
        assert v.audio.trim_goal('second') == -142
        v.rows['main'] = SimpleNamespace(**vars(v.rows['main']))
        v.audio.update_snapshot()
        bar.poll()
        assert not v.audio.sound_trims
        measure.click()
        assert bar.requested
        v.rows['third'] = SimpleNamespace(**vars(v.rows['second']))
        v.rows['third'].decoder = SimpleNamespace(history=object())
        v.rows['third'].calibrate_sound = ConsoleIconButton('sound_calibration', '一键校准比赛声', v)
        v.rows['third'].calibrate_sound.clicked.connect(lambda: bar.calibrate('third'))
        third_caption = v.rows['third'].sound_offset_info = QLabel(v)
        third_caption.hide()
        third_undo = v.rows['third'].undo_sound_calibration = ConsoleIconButton(
            'undo_sound_calibration', '撤销本路声音校准', v)
        third_undo.clicked.connect(lambda: bar.restore('third'))
        bar.poll()
        assert not bar.requested, 'Changing who is in the mix invalidates a pending calibration'
        triple = dict(pairs=[dict(first=i, second=j, windows=[dict(usable=True, lag_ms=lag)] * 4)
                            for i, j, lag in ((0, 1, -44.), (0, 2, 38.), (1, 2, 82.))])
        bar.evidence.add(triple, 0, now.return_value - 24)
        bar.evidence.add(triple, 0, now.return_value - 12)
        bar.poll()
        assert not v.audio.sound_trims and bar.phase == 'ready'
        v.rows['third'].calibrate_sound.click()
        assert (v.audio.trim_goal('second'), v.audio.trim_goal('third')) == (44, -38)
        assert not undo.isHidden() and not third_undo.isHidden()
        assert '声音延后 44 ms' in caption.text() and '声音提前 38 ms' in third_caption.text()
        assert main_caption.isHidden()
        undo.click()
        assert v.audio.trim_goal('second') == 0 and v.audio.trim_goal('third') == -38
        assert undo.isHidden() and caption.isHidden() and not third_undo.isHidden()
        third_undo.click()
        assert not v.audio.trim_goal('second') and not v.audio.trim_goal('third')
        assert third_undo.isHidden()
        del v.rows['third']
        v.audio.update_snapshot()
        bar.poll()
        measure.click()
        assert bar.requested
        now.return_value += 151
        bar.poll()
        assert bar.phase == 'insufficient' and not bar.requested and measure.isEnabled()
        assert not caption.isHidden() and caption.text() == '样本不足' and '样本不足' in measure.toolTip()
        assert not v.audio.sound_trims
    from plugins.match_sync.mix_lag_probe import MixLagProbe
    h1, h2, silent = object(), object(), object()
    probe = MixLagProbe((h1, h2))
    probe.append([(h1, 0), (silent, 0), (h2, 0)], [(b'1111', 40), (b'0000', 0), (b'2222', 40)], 1)
    assert probe.buffers == [b'1111', b'2222']
    e = LagEvidence()
    e.add(result(40), 1, 0)
    assert e.estimate(0, 12) is None
    e.add(result(40), 1, 13)
    # Require distinct measurements, even if several matches arrive in a single batch.
    e.add(dict(pairs=[dict(first=0, second=1, windows=[dict(usable=True, lag_ms=40)])]),1,27)
    assert e.estimate(0, 30) == -40 and e.estimate(0, 70) is None
    # Drive the actual routed/resampled mixer with two copies of one sound.
    # The secondary has a known 48 ms lead. Compare the accepted PCM after
    # the ramp and check every adjacent source read for skips or duplicates.
    import numpy as np
    from plugins.match_sync import audio as audio_module
    sound = np.random.default_rng(10).integers(-5000, 5000, size=RATE*12, dtype=np.int16)
    class Track:
        def __init__(self, lead):
            self.lead, self.end = lead, None
        def pcm_at(self, position, count):
            start = round(position * RATE)
            assert self.end is None or self.end == start
            self.end = start + count
            mono = sound[start+self.lead:start+self.lead+count]
            assert len(mono) == count
            return np.repeat(mono, 2).tobytes()

    v.shifts = lambda: {'main': 0., 'second': 0.}
    for key, lead in [('main', 0), ('second', round(.048 * RATE))]:
        v.rows[key].decoder.history = Track(lead)
    pump = v.audio
    free = [0]
    def available():
        amount, free[0] = free[0], 0
        return amount
    pump.sink = SimpleNamespace(bytesFree=available, processedUSecs=lambda: pump.written * 1e6 / RATE)
    pump.device = SimpleNamespace(write=len)
    real_mix = audio_module.mix_pcm
    final_blocks = [bytearray(), bytearray()]
    identical = []
    def mix(inputs, frames):
        identical.append(inputs[0][0] == inputs[1][0])
        if len(identical) > 850:
            for out, (pcm, _) in zip(final_blocks, inputs):
                out.extend(pcm)
        return real_mix(inputs, frames)
    with patch.object(audio_module, 'mix_pcm', mix), patch('time.monotonic', return_value=100.) as now:
        for i in range(1150):
            if i == 50:
                pump.set_trim('second', 48)
            now.return_value += .01
            free[0] = 480 * 4
            pump.update_snapshot()
            pump._fill()
        from plugins.match_sync.mix_lag_probe import pair_windows
        residual, = pair_windows(*(np.frombuffer(buf, dtype='<i2').reshape(-1, 8).mean(1) for buf in final_blocks))
        assert not any(identical[:50]) and residual['usable'] and abs(residual['lag_ms']) < 1, residual
    pump.sink = pump.device = None
    bar.disable()
    assert not bar.timer.isActive()
    v.audio.stop()
    print('PASS: automatic sampling, one-click batch calibration, verified residual, undo, reconnect and stale evidence')


if __name__ == '__main__':
    run()
