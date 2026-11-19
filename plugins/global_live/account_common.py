"""account common: extracted platform responsibility."""
from ddm import plugins as api


def cookie_value(session, name, domain):
    return next((cookie.value for cookie in session.cookies if cookie.name == name
                 and cookie.domain.lstrip('.') == domain and not cookie.is_expired()), '')


def checked(response, platform):
    if response.status_code in (401, 403):
        raise api.LoginRequiredError(f'{platform} 登录已失效，请重新打开网页登录')
    if response.status_code == 429:
        raise RuntimeError(f'{platform} 请求过于频繁，请稍后重试')
    response.raise_for_status()
    return response.json()


def progress(session, count):
    callback = getattr(session, 'ddm_follow_progress', None)
    if callable(callback):
        callback(count)
