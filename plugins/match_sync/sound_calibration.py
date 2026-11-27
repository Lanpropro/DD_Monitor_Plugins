"""Common-sound calibration from independently matched live audio windows."""
import statistics
import time
from collections import deque

from PySide6.QtCore import QObject, QTimer

from .engine import np
from .mix_lag_probe import MixLagProbe


class LagEvidence:
    def __init__(self):
        self.windows = deque(maxlen=24)

    def add(self, result, reference, started):
        pairs = result['pairs']
        for index, group in enumerate(zip(*(p['windows'] for p in pairs))):
            if not all(w['usable'] for w in group):
                continue
            lags = {(p['first'], p['second']): w['lag_ms'] for p, w in zip(pairs, group)}
            if len(lags) == 3 and abs(lags[0, 1] + lags[1, 2] - lags[0, 2]) > 3:
                continue
            relative = {j: v for (i, j), v in lags.items() if i == reference}
            relative.update({i: -v for (i, j), v in lags.items() if j == reference})
            self.windows.append((started + 3 * (index + 1), started, relative))

    def estimate(self, slot, now):
        recent = [row for row in self.windows if now - row[0] <= 60 and slot in row[2]]
        if len(recent) < 3 or recent[-1][0] < now - 30 or recent[0][1] == recent[-1][1]:
            return None
        values = [r[2][slot] for r in recent[-3:]]
        if recent[-3][1] == recent[-1][1]:
            values.append(next(r[2][slot] for r in reversed(recent) if r[1] != recent[-1][1]))
        if max(values) - min(values) > 8 or min(map(abs, values)) <= 10 < max(map(abs, values)):
            return None
        return round(statistics.median(values))


class SoundCalibration(QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)
        self.scope = None
        self.keys = ()
        self.previous = {}
        self.evidence = LagEvidence()
        self.probe = None
        self.phase = 'idle'
        self.requested = self.verifying = False
        self.feedback_key = None
        self.refresh()

    def enable(self):
        self.timer.start()
        self.poll()

    def disable(self):
        self.timer.stop()
        self.clear_probe()
        self.scope, self.keys = None, ()
        self.previous.clear()
        self.evidence = LagEvidence()
        self.requested = self.verifying = False
        self.feedback_key = None
        self.phase = 'idle'
        self.refresh()

    def sources(self):
        v = self.viewer
        if not v.running or v.replay.active:
            return ()
        return tuple((k, id(r), id(r.decoder.history)) for k, r in v.rows.items() if r.decoder is not None
                     and r.sync_state == '已同步' and not r.paused and k not in v.independent_playback
                     and r.audio_controls[0] and r.audio_controls[2] * r.mix_gain > 0)

    def context(self):
        v = self.viewer
        return (v.generation, v.main.currentData(), self.sources())

    def clear_probe(self):
        if self.probe is not None:
            with self.viewer.audio.lock:
                self.probe.cancel()
            self.probe = None

    def applied(self):
        if not self.viewer.running:
            return {}
        rows = self.viewer.rows
        main = rows.get(self.viewer.main.currentData())
        return {k: goal for k, (ref, row, goal, _current) in self.viewer.audio.sound_trims.items()
                if goal and rows.get(k) is row and main is ref}

    def sample(self, *, verifying=False):
        self.clear_probe()
        self.evidence = LagEvidence()
        self.verifying = verifying
        if self.available() and (not verifying or self.applied().keys() <= set(self.keys)):
            self.phase = 'sampling'
            self.until = time.monotonic() + 150
        else:
            self.phase = 'unavailable' if self.viewer.running else 'idle'

    def available(self):
        return (np is not None and self.scope is not None
                and len(self.keys) in (2, 3) and self.scope[1] in self.keys)

    def sync_scope(self):
        scope = self.context()
        if scope == self.scope:
            return
        self.scope = scope
        self.keys = tuple(key for key, _, _ in scope[2])
        applied = self.applied()
        rows = self.viewer.rows
        self.previous = {key: entry for key, entry in self.previous.items()
                         if entry[0] is rows.get(scope[1]) and entry[1] is rows.get(key)}
        self.requested = False
        self.feedback_key = None
        self.sample(verifying=bool(applied))

    def start(self):
        """Collect fresh evidence for the diagnostic per-source actions."""
        self.sync_scope()
        self.requested = False
        self.sample()
        self.enable()

    def calibrate(self, key):
        self.sync_scope()
        if not self.available() or key not in self.keys:
            return
        self.requested = True
        self.feedback_key = key
        if self.phase != 'sampling':
            if not self.finish_if_ready(time.monotonic()):
                self.sample(verifying=self.verifying)
        else:
            self.until = time.monotonic() + 150
        self.enable()

    def poll(self):
        v = self.viewer
        now = time.monotonic()
        self.sync_scope()
        if self.phase == 'sampling' and self.verifying and not v.audio.trims_settled():
            # Count the evidence window after the smooth audio adjustment settles.
            self.until = now + 150
        elif self.phase == 'sampling' and now >= self.until:
            self.clear_probe()
            self.requested = False
            self.phase = 'insufficient'
        elif self.phase == 'sampling':
            if self.probe is not None and self.probe.result['state'] == 'done':
                self.evidence.add(self.probe.result, self.keys.index(self.scope[1]), self.started)
                self.probe = None
            elif self.probe is not None and (self.probe.result['state'] not in ('collecting', 'analyzing')
                                             or now - self.started > 25):
                self.clear_probe()
            if not self.finish_if_ready(now) and self.probe is None and v.audio.trims_settled():
                with v.audio.lock:
                    current = v.audio.mix_lag_probe
                    if current is None or current.result['state'] not in ('collecting', 'analyzing'):
                        self.probe = v.audio.mix_lag_probe = MixLagProbe([v.rows[k].decoder.history for k in self.keys])
                        self.started = now
        self.refresh()

    def estimate(self, key, now=None):
        if key not in self.keys or key == self.scope[1]:
            return None
        return self.evidence.estimate(self.keys.index(key), time.monotonic() if now is None else now)

    def finish_if_ready(self, now):
        lags = {key: self.estimate(key, now) for key in self.keys if key != self.scope[1]}
        if not lags or any(lag is None for lag in lags.values()):
            return False
        self.clear_probe()
        corrections = {key: lag for key, lag in lags.items() if abs(lag) > 10}
        if self.requested and corrections:
            self.apply_lags(corrections)
        elif self.verifying:
            self.phase = 'residual' if corrections else 'verified'
        else:
            self.phase = 'ready' if corrections else 'aligned'
        self.requested = False
        return True

    def apply_lags(self, lags):
        audio = self.viewer.audio
        rows = self.viewer.rows
        self.previous.update({key: (rows[self.scope[1]], rows[key], audio.trim_goal(key)) for key in lags})
        for key, lag in lags.items():
            audio.set_trim(key, self.previous[key][2] - lag)
        self.sample(verifying=True)

    def restore(self, key):
        self.sync_scope()
        if key not in self.previous:
            return False
        self.viewer.audio.set_trim(key, self.previous.pop(key)[2])
        self.requested = False
        self.feedback_key = None
        self.sample()
        self.refresh()
        return True

    def change(self, key, undo):
        self.sync_scope()
        if undo:
            return self.restore(key)
        lag = self.estimate(key)
        if lag is None or abs(lag) <= 10:
            return False
        self.requested = False
        self.apply_lags({key: lag})
        self.refresh()
        return True

    def refresh(self):
        enabled = self.available() and not self.requested and not (self.phase == 'sampling' and self.verifying)
        applied = self.applied()
        if self.phase == 'unavailable':
            text = '补偿已保留，等待重新入混' if applied else '两至三路完成对齐并入混后可校准'
            brief = '待入混'
        elif self.phase == 'sampling':
            if self.verifying:
                text, brief = (('正在复测共同比赛声…', '复测中') if self.viewer.audio.trims_settled()
                               else ('正在平滑调整声音…', '调整中'))
            else:
                text, brief = (('等待共同比赛声…', '等待比赛声…') if self.requested
                               else ('正在预采样共同比赛声…', ''))
        elif self.phase == 'insufficient':
            text = '复测样本不足，可重试' if self.verifying else '共同比赛声样本不足，可重试'
            brief = '样本不足'
        else:
            text = {'idle': '入混后自动取样', 'ready': '已取样，点击即可校准', 'aligned': '声音已对齐',
                    'verified': '复测通过', 'residual': '复测仍有偏差，可再校准'}[self.phase]
            brief = {'aligned': '声音已对齐', 'verified': '复测通过', 'residual': '仍有偏差'}.get(self.phase, '')
        if self.phase == 'ready' and any(self.estimate(key) is None for key in self.keys if key != self.scope[1]):
            text = '点击即可重新检测'
        if self.phase in ('aligned', 'verified', 'residual'):
            lags = [abs(lag) for key in self.keys if (lag := self.estimate(key)) is not None]
            if lags:
                text += f' · 最大偏差约 {max(lags)} ms'
        offsets = {key: f"声音{'延后' if goal > 0 else '提前'} {abs(goal)} ms" for key, goal in applied.items()}
        self.status_text = text + ''.join(f' · {self.viewer.rows[key].label()} {offset}' for key, offset in offsets.items())
        tooltip = f'一键校准比赛声（当前混音）\n{text}'
        for key, row in self.viewer.rows.items():
            row.undo_sound_calibration.setVisible(key in self.previous)
            offset = offsets.get(key)
            status = row.sound_offset_info
            caption = ('\n'.join(filter(None, (offset, brief))) if offset else
                       brief if key == self.feedback_key and not applied else '')
            if status.text() != caption:
                status.setText(caption)
                status.setVisible(bool(caption))
            detail = f'{offset}\n{text}' if offset else text if caption else ''
            if status.toolTip() != detail:
                status.setToolTip(detail)
            button = row.calibrate_sound
            button.setEnabled(enabled and key in self.keys)
            hint = ('一键校准比赛声（当前混音）\n本路加入混音后可用'
                    if enabled and key not in self.keys else tooltip)
            if button.toolTip() != hint:
                button.setToolTip(hint)
