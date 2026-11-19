"""Actionable subscription API errors without exposing Google's response body."""


MESSAGES = {
    'insufficientPermissions': '未授予 YouTube 读取权限；请点击「重新授权」，并勾选查看 YouTube 账号的权限',
    'ACCESS_TOKEN_SCOPE_INSUFFICIENT': '未授予 YouTube 读取权限；请点击「重新授权」，并勾选查看 YouTube 账号的权限',
    'accessNotConfigured': '当前客户端所属项目尚未启用 YouTube Data API v3，或启用设置尚未生效',
    'SERVICE_DISABLED': '当前客户端所属项目尚未启用 YouTube Data API v3，或启用设置尚未生效',
    'quotaExceeded': 'YouTube API 配额已用尽，请稍后重试或检查项目配额',
    'dailyLimitExceeded': 'YouTube API 当日配额已用尽，请等待配额恢复',
    'youtubeSignupRequired': '当前 Google 账号尚未关联 YouTube 频道，请先在 YouTube 完成频道设置',
    'subscriptionForbidden': 'YouTube 拒绝读取当前账号的订阅，请确认授权的 YouTube 账号',
    'authError': 'Google 登录已失效，请点击「重新授权」',
    'authorizationRequired': 'Google 登录已失效，请点击「重新授权」',
    'forbidden': 'YouTube 拒绝此次订阅读取，请检查账号权限',
}


def subscription_error(response):
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get('error', {}) if isinstance(payload, dict) else {}
    reasons = []
    if isinstance(error, dict):
        for field in ('errors', 'details'):
            entries = error.get(field, [])
            if isinstance(entries, list):
                reasons.extend(item.get('reason') for item in entries if isinstance(item, dict))
    reason = next((value for value in reasons if isinstance(value, str) and value in MESSAGES), '')
    message = MESSAGES[reason] if reason else (
        'Google 登录已失效，请点击「重新授权」' if response.status_code == 401 else
        'YouTube 订阅读取被拒绝，平台未返回可识别的原因')
    code = f'HTTP {response.status_code}' + (f' / {reason}' if reason else '')
    return f'{message}（{code}）'
