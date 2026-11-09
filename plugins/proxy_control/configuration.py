"""Validate user inputs and build an isolated core configuration."""
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, unquote

MAX_PROFILE = 4 * 1024 * 1024


def proxy_address(value):
    try:
        parts = urlsplit(value.strip())
        if (parts.scheme not in ('http', 'socks5', 'socks5h') or not parts.hostname or
                not parts.port or parts.path not in ('', '/') or parts.query or parts.fragment or
                any(char.isspace() for char in value)):
            raise ValueError
        return parts
    except ValueError:
        raise ValueError('请输入 http://主机:端口 或 socks5://主机:端口，可包含用户名与密码') from None


def subscription_url(value):
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
            raise ValueError
        if any(char.isspace() for char in value):
            raise ValueError
        parts.port
        return value.strip()
    except ValueError:
        raise ValueError('请输入有效的 HTTP(S) 订阅地址') from None


@dataclass
class Options:
    mode: str = 'system'
    address: str = ''
    core: str = ''
    source: str = 'url'
    profile: str = ''
    node: str = ''

    def validate(self):
        if self.mode not in ('system', 'direct', 'external', 'managed'):
            raise ValueError('请选择代理方式')
        if self.mode == 'external':
            proxy_address(self.address)
        if self.mode == 'managed':
            if self.source == 'url':
                subscription_url(self.profile)
            elif self.source == 'file':
                path = Path(self.profile)
                if not path.is_file() or path.stat().st_size > MAX_PROFILE:
                    raise ValueError('请选择不超过 4 MiB 的节点配置文件')
            elif self.source == 'nodes':
                if not self.profile.strip() or len(self.profile.encode('utf-8')) > MAX_PROFILE:
                    raise ValueError('请粘贴节点链接，内容不得超过 4 MiB')
            else:
                raise ValueError('请选择订阅、配置文件或节点链接')
        if self.needs_core() and not Path(self.core).is_file():
            raise ValueError('请先点击“准备内核”，或选择已有的 Mihomo 程序')

    def needs_core(self):
        return self.mode == 'managed' or (self.mode == 'external' and
                                         proxy_address(self.address).scheme.startswith('socks'))


def core_config(options, mixed_port, api_port, secret):
    config = {'mixed-port': mixed_port, 'bind-address': '127.0.0.1', 'allow-lan': False,
              'external-controller': f'127.0.0.1:{api_port}', 'secret': secret,
              'mode': 'rule', 'log-level': 'silent', 'ipv6': True,
              'find-process-mode': 'off', 'tun': {'enable': False}, 'dns': {'enable': False},
              'profile': {'store-selected': False, 'store-fake-ip': False},
              'rules': ['MATCH,DDM']}
    group = {'name': 'DDM', 'type': 'select'}
    if options.mode == 'external':
        parts = proxy_address(options.address)
        node = {'name': 'External', 'type': 'socks5', 'server': parts.hostname,
                'port': parts.port, 'udp': False}
        if parts.username is not None:
            node['username'] = unquote(parts.username)
            node['password'] = unquote(parts.password or '')
        config['proxies'] = [node]
        group['proxies'] = ['External']
    else:
        config['proxy-providers'] = {'subscription': {'type': 'file', 'path': 'nodes.txt',
                                                       'health-check': {'enable': False}}}
        group['use'] = ['subscription']
    config['proxy-groups'] = [group]
    return config


def save_options(root, options):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # Personal data belongs outside the install package and host settings export.
    path = root / 'preferences.json'
    temporary = root / 'preferences.json.tmp'
    temporary.write_text(json.dumps(asdict(options), ensure_ascii=False), encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_options(root):
    try:
        return Options(**json.loads((Path(root) / 'preferences.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        return Options()
