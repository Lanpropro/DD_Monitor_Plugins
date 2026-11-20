"""Read official Qie TV room metadata and hand an authorized source to the host."""
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

import requests

from ddm import plugins as api


ORIGIN = 'https://www.qie.tv/'
USER_AGENT = 'Mozilla/5.0'
ROOM_PATH = re.compile(r'/([1-9][0-9]*)/?')
ROOM_ID = re.compile(r'qie:([1-9][0-9]*)')
SCRIPTS = re.compile(r'<script\b[^>]*>(\s*\{.*?)</script\s*>', re.IGNORECASE | re.DOTALL)
QUALITY_720P = 720


class QieError(RuntimeError):
    @property
    def user_message(self):
        return str(self)


def room_number(value):
    text = str(value or '').strip()
    if match := ROOM_ID.fullmatch(text):
        return match[1]
    try:
        url = urlsplit(text)
        if (url.scheme == 'https' and url.netloc.lower() in ('www.qie.tv', 'qie.tv')
                and (match := ROOM_PATH.fullmatch(url.path))):
            return match[1]
    except ValueError:
        pass
    return ''


def room_payload(page, number):
    for script in SCRIPTS.findall(page):
        try:
            result = json.loads(script)['props']['initialState']['roomInfo']['roomInfo']
            if isinstance(result, dict):
                break
        except (ValueError, KeyError, TypeError):
            continue
    else:
        raise QieError('无法读取企鹅体育房间资料，请稍后重试')
    if result.get('code') == 404:
        raise QieError('企鹅体育房间不存在，请检查房间链接')
    if result.get('code') in (401, 403):
        raise QieError('企鹅体育限制访问该房间，请在官网确认观看权限')
    room = result.get('room_info')
    if (not isinstance(room, dict) or str(room.get('room_id')) != number
            or str(room.get('is_live')) not in ('0', '1')):
        raise QieError('企鹅体育房间资料不完整，请稍后重试')
    return room, result.get('pay_info') or {}


def cdn_url(value):
    if not isinstance(value, str) or not value:
        return ''
    try:
        url = urlsplit(urljoin(ORIGIN, value))
    except ValueError:
        return ''
    if (url.scheme in ('https', 'http') and url.netloc.lower().endswith('.qiecdn.com')
            and url.username is None and url.password is None):
        return url.geturl()
    return ''


class QiePlatform(api.Platform):
    kind = 'qie'
    label = '企鹅体育'
    room_input_hint = '企鹅体育直播间链接，例如 https://www.qie.tv/10202119'

    def __init__(self, context=None, resolve_source=None):
        self.context = context
        self.resolve_source = resolve_source

    def matches(self, room_id):
        return bool(room_number(room_id))

    def normalize(self, room_id):
        number = room_number(room_id)
        if not number:
            raise ValueError('请输入企鹅体育官方房间链接')
        return self.kind + ':' + number

    def room_url(self, room_id):
        return ORIGIN + self.normalize(room_id).split(':', 1)[1]

    def room_card_branding(self, room_id):
        return {'badge_path': str(Path(__file__).with_name('icon.png')), 'label': self.label}

    def danmaku_client(self, room_id, parent=None):
        from .danmaku import QieDanmakuClient
        number = room_number(self.normalize(room_id))
        return QieDanmakuClient(number, parent)

    def _room(self, room_id):
        number = self.normalize(room_id).split(':', 1)[1]
        try:
            with requests.get(ORIGIN + number, headers={'User-Agent': USER_AGENT},
                              timeout=(4, 6)) as response:
                if response.status_code in (401, 403, 451):
                    raise QieError('企鹅体育限制访问该房间，请在官网确认观看权限')
                if response.status_code == 404:
                    raise QieError('企鹅体育房间不存在，请检查房间链接')
                response.raise_for_status()
                return room_payload(response.content.decode('utf-8', 'replace'), number)
        except requests.RequestException as error:
            raise QieError('企鹅体育房间资料请求失败，请检查网络后重试') from error

    def room_info(self, room_id):
        canonical = self.normalize(room_id)
        room, _ = self._room(canonical)
        face = cdn_url(room.get('owner_avatar'))
        if not face:
            from .mobile_source import fetch_mobile_room
            try:
                mobile = fetch_mobile_room(room_number(canonical))
                face = cdn_url(mobile.get('owner_avatar'))
            except QieError:
                pass  # A missing avatar must not hide valid live status or playback.
        return api.RoomInfo(room_id=canonical, uname=room.get('nickname') or self.label,
                            title=room.get('room_name') or self.label,
                            live=str(room['is_live']) == '1', viewers=str(room.get('room_hotv') or ''),
                            cover_url=cdn_url(room.get('room_src')), face=face,
                            platform=self.kind, extra={'live_known': True})

    def rooms_status(self, room_ids):
        results = {}
        for room_id in room_ids:
            try:
                results[room_id] = self.room_info(room_id).as_dict()
            except QieError:
                continue  # A failed lookup must not change the last known status.
        return results

    def room_quality_options(self, room_id):
        return [{'qn': QUALITY_720P, 'desc': '720p', 'label': '720p'}]

    def play_url(self, room_id, quality=QUALITY_720P, *, cancelled=None):
        number = room_number(room_id)
        room, pay = self._room(room_id)
        if str(room['is_live']) != '1':
            raise QieError('企鹅体育房间尚未开播')
        if isinstance(pay, dict) and str(pay.get('status')) == '1' and str(pay.get('is_allow')) == '0':
            raise QieError('企鹅体育限制访客观看此直播，请在官网确认观看权限')
        if not callable(getattr(self.context, 'retain_media_source', None)):
            raise QieError('当前宿主缺少媒体交接接口，请更新 DD 监控室 CE')

        def prepare(is_cancelled):
            if is_cancelled():
                raise QieError('企鹅体育取流已取消')
            if self.resolve_source is None:
                from .mobile_source import open_mobile_720p
                return open_mobile_720p(number, is_cancelled)
            from .media import open_720p
            address = self.resolve_source(number, is_cancelled)
            return open_720p(address, number, is_cancelled)

        def reopen(is_cancelled):
            try:
                return prepare(is_cancelled)
            except QieError as error:
                raise OSError(str(error)) from error

        prepared = prepare(cancelled or (lambda: False))
        try:
            if cancelled is not None and cancelled():
                raise QieError('企鹅体育取流已取消')
            local_url = self.context.retain_media_source(reopen, prepared)
        except Exception:
            prepared.close()
            raise
        return local_url, QUALITY_720P, self.kind

    def preview_url(self, room_id, *, cancelled=None):
        return self.play_url(room_id, cancelled=cancelled)
