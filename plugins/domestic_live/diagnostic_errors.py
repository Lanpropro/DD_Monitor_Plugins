"""Redacted exception categories shared by adapters and playback observation."""
import requests
from .douyin_errors import DouyinLookupError


def diagnostic_reason(error):
    """仅记录错误类别；原始异常可能带签名 URL、代理密码和 Cookie。"""
    reasons, seen = [], set()
    while error is not None and id(error) not in seen and len(reasons) < 5:
        seen.add(id(error))
        detail = type(error).__name__
        if isinstance(error, requests.HTTPError) and error.response is not None:
            detail += f"(HTTP {error.response.status_code})"
        elif isinstance(error, requests.exceptions.SSLError):
            detail += "(证书校验失败)" if "CERTIFICATE_VERIFY_FAILED" in str(error) else "(TLS连接失败)"
        elif isinstance(error, requests.exceptions.ProxyError):
            detail += "(代理连接失败)"
        elif isinstance(error, requests.ConnectionError):
            if any(token in str(error) for token in ("getaddrinfo failed", "NameResolutionError")):
                detail += "(域名解析失败)"
        elif isinstance(error, DouyinLookupError):
            detail += f"({error.user_message})"
        reasons.append(detail)
        error = error.__cause__ or error.__context__
    return " <- ".join(reasons)
