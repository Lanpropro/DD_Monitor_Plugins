"""youtube accounts: extracted platform responsibility."""
import hashlib
import json
import re
import time
from ddm import plugins as api
from ddm.global_danmaku import page_json, renderers
from ddm.live_danmaku import USER_AGENT
from .account_common import cookie_value, checked, progress

def youtube_context(session):
    with session.get('https://www.youtube.com/feed/channels', timeout=(4, 8),
                     headers={'User-Agent': USER_AGENT}) as reply:
        reply.raise_for_status()
        page = reply.text
    config = {}
    for match in re.finditer(r'ytcfg\.set\s*\(\s*', page):
        value, _ = json.JSONDecoder().raw_decode(page[match.end():])
        if isinstance(value, dict):
            config.update(value)
    if not config.get('LOGGED_IN'):
        raise api.LoginRequiredError('请先在 YouTube 官方页面完成登录')
    context = config.get('INNERTUBE_CONTEXT')
    if not isinstance(context, dict) or not isinstance(context.get('client'), dict):
        raise RuntimeError('YouTube 网页接口结构已变化，可使用 OAuth 授权读取')
    sid = cookie_value(session, 'SAPISID', 'youtube.com') or cookie_value(session, '__Secure-3PAPISID', 'youtube.com')
    if not sid:
        raise api.LoginRequiredError('YouTube 登录状态不完整，请重新打开网页登录')
    stamp = str(int(time.time()))
    signature = hashlib.sha1(f'{stamp} {sid} https://www.youtube.com'.encode()).hexdigest()
    headers = {'Authorization': f'SAPISIDHASH {stamp}_{signature}', 'Origin': 'https://www.youtube.com',
               'X-Origin': 'https://www.youtube.com', 'User-Agent': USER_AGENT,
               'X-Goog-AuthUser': str(config.get('SESSION_INDEX', 0))}
    if config.get('DELEGATED_SESSION_ID'):
        headers['X-Goog-PageId'] = str(config['DELEGATED_SESSION_ID'])
    return config, headers, page_json(page, 'ytInitialData')


def youtube_query(session, path, config, headers, **params):
    with session.post('https://www.youtube.com/youtubei/v1/' + path, timeout=(4, 8),
            headers=headers, json={'context': config['INNERTUBE_CONTEXT'], **params}) as reply:
        return checked(reply, 'YouTube')


def youtube_account(session, cancelled):
    if cancelled():
        return {}
    config, headers, _ = youtube_context(session)
    if cancelled():
        return {}
    data = youtube_query(session, 'account/account_menu', config, headers)
    account = next(renderers(data, 'activeAccountHeaderRenderer'), None)
    if account is None:
        raise api.LoginRequiredError('未确认 YouTube 登录身份，请在官方页面完成登录')
    name = account.get('accountName', {}).get('simpleText') or ''.join(
        part.get('text', '') for part in account.get('accountName', {}).get('runs', []))
    identity = config.get('DELEGATED_SESSION_ID') or config.get('DATASYNC_ID')
    if not name or not isinstance(identity, str) or not identity:
        raise RuntimeError('YouTube 未返回稳定账号身份，可使用 OAuth 授权')
    photos = account.get('accountPhoto', {}).get('thumbnails', [])
    # An opaque local ID avoids exposing YouTube's internal session identifiers in the UI.
    uid = 'web-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return {'uid': uid, 'uname': name, 'face': photos[-1].get('url', '') if photos else ''}


def youtube_follows(session, cancelled):
    if cancelled():
        return []
    config, headers, data = youtube_context(session)
    rooms, cursors = {}, set()
    while not cancelled():
        contents = data.get('contents', data)
        for channel in renderers(contents, 'channelRenderer'):
            if cancelled():
                return []
            cid = channel.get('channelId', '')
            if not isinstance(cid, str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', cid):
                raise RuntimeError('YouTube 订阅列表缺少有效频道 ID')
            title = channel.get('title', {})
            name = title.get('simpleText') or ''.join(part.get('text', '') for part in title.get('runs', []))
            photos = channel.get('thumbnail', {}).get('thumbnails', [])
            rid = 'youtube:' + cid
            rooms[rid] = api.RoomInfo(room_id=rid, platform='youtube', uname=name or cid,
                face=photos[-1].get('url', '') if photos else '',
                extra={'live_known': False, 'playback_mode': 'stream'}).as_dict()
        progress(session, len(rooms))
        if cancelled():
            return []
        tokens = [item.get('token') for item in renderers(contents, 'continuationCommand') if item.get('token')]
        if not tokens:
            # The subscriptions-management page also has an explicit empty state.
            if not rooms and not list(renderers(contents, 'messageRenderer')):
                raise RuntimeError('未识别到 YouTube 订阅列表，可重试或使用 OAuth 授权')
            return list(rooms.values())
        if len(set(tokens)) != 1 or not isinstance(tokens[0], str) or tokens[0] in cursors:
            raise RuntimeError('YouTube 订阅分页没有前进，请稍后重试')
        cursors.add(tokens[0])
        data = youtube_query(session, 'browse', config, headers, continuation=tokens[0])
    return []
