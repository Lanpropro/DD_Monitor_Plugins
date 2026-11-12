"""On-demand cached-video sampling and local Windows OCR; no live connections."""
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from .video import av, image_from_frame, np
from .clock_sync import parse_clock


def capture_band(history, anchor, crop, cancelled, *, preview=False):
    if cancelled.is_set():
        return []
    start = anchor if preview else anchor - 1.5
    segment = history.segment_at(start)
    if segment is None or segment is not history.segment_at(anchor):
        raise ValueError('所选播放位置的连续缓存不足，请等待缓存后重新采样')
    history = segment
    origin = history.origin
    result = history.packet_range(None, start - origin)
    if result is None:
        raise ValueError('该位置缺少关键帧，请等待缓存后重新采样')
    config, packets, _reset = result
    codec = av.CodecContext.create(config[0], 'r')
    codec.extradata = config[1]
    codec.thread_count = 1
    codec.thread_type = 'SLICE'
    frames = []
    deadline = time.monotonic() + 12
    last = float('-inf')
    while packets and not cancelled.is_set():
        for packet in packets:
            if cancelled.is_set():
                return []
            if time.monotonic() > deadline:
                raise ValueError('取帧超时，请缩小采样范围后重试')
            for frame in codec.decode(packet.decode_packet()):
                stamp = origin + float(frame.pts * frame.time_base)
                if stamp < start - .001:
                    continue
                if preview:
                    return [(stamp, image_from_frame(frame, width=640))]
                if stamp > anchor + .001:
                    if len(frames) < 12 or frames[-1][0] - frames[0][0] < 1.1:
                        raise ValueError('连续画面不足，请重新采样')
                    return frames
                if stamp - last < .065:
                    continue
                # Limit the full-frame conversion before applying the normalized
                # crop; a 4K source must not become a 4K QImage on the worker.
                image = image_from_frame(frame, width=1280)
                x, y, width, height = crop
                image = image.copy(round(x * image.width()), round(y * image.height()),
                                   max(1, round(width * image.width())), max(1, round(height * image.height())))
                image = image.scaled(480, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                frames.append((stamp, image))
                last = stamp
        result = history.packet_range(packets[-1].sequence + 1, start - origin)
        if result is None or result[2]:
            break
        packets = result[1]
    if len(frames) >= 12 and frames[-1][0] - frames[0][0] >= 1.1:
        return frames
    raise ValueError('未能取得连续计时器画面，请重新采样')


def ocr_variants(image):
    gray = image.convertToFormat(QImage.Format_Grayscale8)
    pixels = np.frombuffer(gray.constBits(), dtype=np.uint8).reshape(gray.height(), gray.bytesPerLine())[:, :gray.width()]
    ink = pixels > 128 if np.median(pixels) < 128 else pixels < 128
    binary = np.where(ink, 0, 255).astype(np.uint8)
    normalized = QImage(binary.data, gray.width(), gray.height(), binary.strides[0], QImage.Format_Grayscale8).copy()
    variants = [('full', normalized)]
    columns = np.flatnonzero(ink.any(axis=0))
    runs = np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1)
    if 4 <= len(runs) <= 6 and all(len(run) for run in runs):
        colon = runs[-3]
        rows = np.flatnonzero(ink[:, colon].any(axis=1))
        all_rows = np.flatnonzero(ink.any(axis=1))
        if (len(rows) and len(all_rows) and colon[-1] - colon[0] < .3 * len(all_rows)
                and rows[-1] - rows[0] < .8 * (all_rows[-1] - all_rows[0])):
            variants.extend((('minutes', normalized.copy(int(runs[0][0]), 0, int(colon[0] - runs[0][0]), gray.height())),
                             ('seconds', normalized.copy(int(runs[-2][0]), 0, int(runs[-1][-1] - runs[-2][0] + 1), gray.height()))))
    padded = []
    for part, bitmap in variants:
        bitmap = bitmap.scaled(480, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QImage(bitmap.width() + 32, bitmap.height() + 32, QImage.Format_RGB32)
        canvas.fill(Qt.white)
        painter = QPainter(canvas)
        painter.drawImage(16, 16, bitmap)
        painter.end()
        padded.append((part, canvas))
    return padded


def recognize_bands(bands, cancelled):
    if cancelled.is_set():
        return {}, '识别已取消'
    if os.name != 'nt':
        return {}, '当前系统没有 Windows OCR，请手填首帧时间并选择翻秒帧'
    with tempfile.TemporaryDirectory(prefix='ddm-clock-') as directory:
        order = []
        for key, frames in bands.items():
            for index, (_stamp, image) in enumerate(frames):
                if cancelled.is_set():
                    return {}, '识别已取消'
                for part, padded in ocr_variants(image):
                    name = f'{len(order):04d}.png'
                    if not padded.save(str(Path(directory) / name)):
                        raise ValueError('无法准备计时器图片')
                    order.append((key, index, part))
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(Path(__file__).with_name('clock_ocr.ps1')), '-ImageDirectory', directory]
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(command, stdout=output, stderr=errors,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError:
                return {}, 'Windows OCR 无法启动，请手填首帧时间并选择翻秒帧'
            deadline = time.monotonic() + 40
            while process.poll() is None:
                if cancelled.wait(.05) or time.monotonic() > deadline:
                    process.kill()
                    process.wait()
                    return {}, '识别已取消或超时，可手动核对'
            if process.returncode:
                return {}, 'Windows OCR 不可用，请手填首帧时间并选择翻秒帧'
            output.seek(0)
            try:
                texts = json.loads(output.read().decode('utf-8-sig'))
                if len(texts) != len(order):
                    raise ValueError('Unexpected OCR result count')
            except (ValueError, UnicodeError):
                return {}, '未能识别时间，请手动核对'
        readings = {key: [None] * len(frames) for key, frames in bands.items()}
        parts = {}
        for (key, index, part), text in zip(order, texts):
            parts.setdefault((key, index), {})[part] = text.strip()
        for (key, index), text in parts.items():
            full = parse_clock(text['full'])
            minutes, seconds = text.get('minutes', ''), text.get('seconds', '')
            split = (int(minutes) * 60 + int(seconds) if re.fullmatch(r'\d{1,3}', minutes)
                     and re.fullmatch(r'[0-5]?\d', seconds) else None)
            readings[key][index] = (None if full is not None and split is not None and full != split
                                   else full if full is not None else split)
        return readings, ''
