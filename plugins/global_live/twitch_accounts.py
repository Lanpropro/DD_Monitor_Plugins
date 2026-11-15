"""twitch accounts: extracted platform responsibility."""
import re
from ddm import plugins as api
from ddm.live_danmaku import USER_AGENT
from streamlink.plugins.twitch import TwitchAPI
from .public_live import image_url
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


class TwitchAccounts:
    oauth_provider = "twitch"

    account_login_url = "https://www.twitch.tv/login"

    account_cookie_domain = "twitch.tv"

    follow_login_url = "https://www.twitch.tv/directory/following/channels"

    follow_cookie_domain = "twitch.tv"

    browser_login_url = "https://www.twitch.tv/login"

    browser_cookie_domains = ("twitch.tv",)

    def browser_account_info(self, session, cancelled):
        return twitch_account(session, cancelled)

    def browser_follow_rooms(self, session, cancelled):
        return twitch_follows(session, cancelled)

    def _authorization(self, session):
        token = next((cookie.value for cookie in session.cookies if cookie.name == "auth-token"
                      and (cookie.domain.lstrip(".") == "twitch.tv"
                           or cookie.domain.lstrip(".").endswith(".twitch.tv"))), "")
        if not token:
            raise RuntimeError("Twitch 未登录，请在官方页面完成登录")
        with session.get("https://id.twitch.tv/oauth2/validate",
                headers={"Authorization": "OAuth " + token}, timeout=(4, 8)) as response:
            if response.status_code == 401:
                raise RuntimeError("Twitch 登录已过期，请在官方页面重新登录")
            response.raise_for_status()
            identity = response.json()
        uid = str(identity.get("user_id") or "")
        client = identity.get("client_id")
        expected = getattr(session, "ddm_oauth_client_id", "")
        if expected and client != expected:
            raise RuntimeError("Twitch 授权客户端不匹配，请重新授权")
        if not uid.isdigit() or int(uid) <= 0 or not client or not identity.get("login"):
            raise RuntimeError("Twitch 未返回有效用户账号，请重新登录")
        return identity, token

    def account_info(self, session, cancelled) -> dict:
        if cancelled():
            return {}
        identity, token = self._authorization(session)
        uid, client = str(identity["user_id"]), identity["client_id"]
        if cancelled():
            return {}
        with session.get("https://api.twitch.tv/helix/users", params={"id": uid},
                headers={"Authorization": "Bearer " + token, "Client-ID": client},
                timeout=(4, 8)) as response:
            if response.status_code == 401:
                raise RuntimeError("Twitch 登录已过期，请在官方页面重新登录")
            response.raise_for_status()
            users = response.json().get("data", [])
        if len(users) != 1 or str(users[0].get("id")) != uid:
            raise RuntimeError("Twitch 账号信息不匹配，请重新登录")
        user = users[0]
        return {"uid": uid, "uname": user.get("display_name") or identity["login"],
                "face": image_url(user.get("profile_image_url"), ("jtvnw.net",))}

    def follow_rooms(self, session, cancelled) -> list:
        if cancelled():
            return []
        identity, token = self._authorization(session)
        if "user:read:follows" not in identity.get("scopes", []):
            raise RuntimeError("Twitch 账号已登录，但当前授权缺少 user:read:follows 权限，无法读取关注列表。"
                               "普通网页登录不保证包含此权限，需要另行接入 Twitch OAuth 授权。")
        rooms, seen, cursors = [], set(), set()
        cursor = ""
        while not cancelled():
            params = {"user_id": str(identity["user_id"]), "first": 100}
            if cursor:
                params["after"] = cursor
            with session.get("https://api.twitch.tv/helix/channels/followed", params=params,
                    headers={"Authorization": "Bearer " + token, "Client-ID": identity["client_id"]},
                    timeout=(4, 8)) as response:
                if response.status_code in (401, 403):
                    raise RuntimeError("Twitch 关注读取未获授权，请重新授权 user:read:follows 权限")
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload.get("data"), list):
                raise RuntimeError("Twitch 关注列表返回异常，请稍后重试")
            for item in payload["data"]:
                login = str(item.get("broadcaster_login") or "")
                if not re.fullmatch(r"[A-Za-z0-9_]{1,25}", login):
                    raise RuntimeError("Twitch 关注列表缺少有效频道名，请稍后重试")
                rid = self.normalize("twitch:" + login)
                if rid not in seen:
                    seen.add(rid)
                    rooms.append(api.RoomInfo(room_id=rid, uname=item.get("broadcaster_name") or login,
                                  platform=self.kind, extra={"live_known": False,
                                  "playback_mode": self.playback_mode}).as_dict())
            cursor = payload.get("pagination", {}).get("cursor", "")
            if not cursor:
                return rooms
            if cursor in cursors:
                raise RuntimeError("Twitch 关注分页没有前进，请稍后重试")
            cursors.add(cursor)
        return []
