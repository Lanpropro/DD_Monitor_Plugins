"""twitch accounts: extracted platform responsibility."""
import re
from ddm import plugins as api
from ddm.live_danmaku import USER_AGENT
from streamlink.plugins.twitch import TwitchAPI
from .account_common import cookie_value, checked, progress

def twitch_query(session, query, variables):
    token = cookie_value(session, 'auth-token', 'twitch.tv')
    if not token:
        raise api.LoginRequiredError('请先在 Twitch 官方页面登录')
    with session.post('https://gql.twitch.tv/gql', timeout=(4, 8),
            headers={'Authorization': 'OAuth ' + token, 'Client-ID': TwitchAPI.CLIENT_ID,
                     'User-Agent': USER_AGENT}, json={'query': query, 'variables': variables}) as reply:
        data = checked(reply, 'Twitch')
    if data.get('errors'):
        raise RuntimeError('Twitch 网页账号接口暂不可用，可重试或使用 OAuth 授权')
    return data.get('data') or {}


def twitch_account(session, cancelled):
    if cancelled():
        return {}
    data = twitch_query(session, 'query { currentUser { id login displayName profileImageURL(width:300) } }', {})
    user = data.get('currentUser')
    if not isinstance(user, dict) or not str(user.get('id', '')).isdigit():
        raise api.LoginRequiredError('未确认 Twitch 登录身份，请在官方页面完成登录')
    return {'uid': str(user['id']), 'uname': user.get('displayName') or user.get('login') or 'Twitch',
            'face': user.get('profileImageURL', '')}


def twitch_follows(session, cancelled):
    query = '''query($after:Cursor){currentUser{follows(first:100,after:$after,order:DESC){
        edges{cursor node{id login displayName profileImageURL(width:300)}} pageInfo{hasNextPage}}}}'''
    rooms, cursors, cursor = {}, set(), None
    while not cancelled():
        data = twitch_query(session, query, {'after': cursor})
        user = data.get('currentUser')
        if user is None:
            raise api.LoginRequiredError('Twitch 登录已失效，请重新打开网页登录')
        follows = user.get('follows', {})
        edges = follows.get('edges')
        if not isinstance(edges, list) or not isinstance(follows.get('pageInfo'), dict):
            raise RuntimeError('Twitch 关注列表结构已变化，可使用 OAuth 授权读取')
        for edge in edges:
            if cancelled():
                return []
            user = edge.get('node') or {}
            login = user.get('login', '')
            if not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,25}', login):
                raise RuntimeError('Twitch 关注列表缺少有效频道名')
            rid = 'twitch:' + login.lower()
            rooms[rid] = api.RoomInfo(room_id=rid, platform='twitch',
                uname=user.get('displayName') or login, face=user.get('profileImageURL', ''),
                extra={'live_known': False, 'playback_mode': 'stream'}).as_dict()
        progress(session, len(rooms))
        if cancelled():
            return []
        if not follows['pageInfo'].get('hasNextPage'):
            return list(rooms.values())
        cursor = edges[-1].get('cursor') if edges else None
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise RuntimeError('Twitch 关注分页没有前进，请稍后重试')
        cursors.add(cursor)
    return []
