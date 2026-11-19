"""Typed lookup/verification results shared by platform and diagnostic layers."""


class DouyinLookupError(RuntimeError):
    """可直接向用户展示的诊断，不包含响应正文、凭证或签名 URL。"""

    @property
    def user_message(self):
        return str(self)



class DouyinVerificationRequired(DouyinLookupError):
    browser_verification = True
