"""Process-local proxy routing; never modify Windows proxy settings."""
import os


KEYS = tuple(name for base in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
             for name in (base, base.upper()))
LOOPBACK = 'localhost,127.0.0.1,::1'


class Environment:
    def __init__(self, environ=None):
        self.environ = os.environ if environ is None else environ
        self.original = {key: self.environ.get(key) for key in KEYS}

    def restore(self):
        for key, value in self.original.items():
            if value is None:
                self.environ.pop(key, None)
            else:
                self.environ[key] = value

    def apply(self, endpoint):
        self.restore()
        for key in KEYS:
            self.environ.pop(key, None)
        for key in ('http_proxy', 'https_proxy', 'all_proxy'):
            self.environ[key] = self.environ[key.upper()] = endpoint
        self.environ['no_proxy'] = self.environ['NO_PROXY'] = LOOPBACK

    def child_environment(self):
        # Avoid routing the core's own outbound connections back into itself.
        return {key: value for key, value in os.environ.items() if key not in KEYS}
