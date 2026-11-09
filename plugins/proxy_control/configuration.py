"""Validate user inputs and build an isolated core configuration."""
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

MAX_PROFILE = 4 * 1024 * 1024
DEFAULT_USER_AGENT = 'clash.meta/1.19.33 DDM-Proxy/0.1'
USER_AGENTS = [('Mihomo（默认）', DEFAULT_USER_AGENT), ('Clash Verge', 'clash-verge'), ('FlClash', 'FlClash')]
TEST_TARGETS = {'google': 'https://www.gstatic.com/generate_204',
                'youtube': 'https://www.youtube.com/generate_204',
                'cloudflare': 'http://cp.cloudflare.com/generate_204'}
DOMESTIC_DOMAINS = ('bilibili.com', 'biliapi.net', 'biliapi.com', 'bilivideo.com', 'bilivideo.cn',
                    'hdslb.com', 'acgvideo.com', 'huya.com', 'huyacdn.com', 'msstatic.com',
                    'douyu.com', 'douyucdn.cn', 'douyuscdn.com', 'douyin.com', 'douyincdn.com',
                    'douyinpic.com', 'byteimg.com', 'bytedance.com', 'snssdk.com', 'amemv.com',
                    'bdxiguastatic.com', 'pstatp.com', 'toutiao.com')


def subscription_url(value):
    value = value.strip()
    if any(char.isspace() for char in value):
        raise ValueError('订阅地址必须是完整的一行链接，请去掉中间空格或换行')
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
            raise ValueError
        parts.port
        return value
    except ValueError:
        raise ValueError('请输入有效的 HTTP(S) 订阅地址') from None


@dataclass
class Options:
    enabled: bool = False
    core: str = ''
    source: str = 'url'
    profile: str = ''
    node: str = ''
    profile_id: str = ''
    profile_name: str = '我的配置'
    auto_start: bool = True
    routing: str = 'rule'
    test_target: str = 'google'
    user_agent: str = DEFAULT_USER_AGENT

    def validate(self):
        if self.source == 'url' and (
                not isinstance(self.user_agent, str) or not self.user_agent.strip() or len(self.user_agent) > 256
                or any(ord(char) < 32 or ord(char) > 126 for char in self.user_agent)):
            raise ValueError('请输入 1～256 个可打印 ASCII 字符的 User-Agent，不可包含换行')
        if self.test_target not in TEST_TARGETS:
            raise ValueError('请选择有效的检测地址')
        if self.routing not in ('rule', 'global', 'direct'):
            raise ValueError('请选择规则、全局或直连模式')
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
        if not Path(self.core).is_file():
            raise ValueError('请先点击“准备内核”，或选择已有的 Mihomo 程序')


def core_config(mixed_port, api_port, secret, routing='rule'):
    config = {'mixed-port': mixed_port, 'bind-address': '127.0.0.1', 'allow-lan': False,
              'external-controller': f'127.0.0.1:{api_port}', 'secret': secret,
              'mode': routing, 'log-level': 'warning', 'ipv6': False,
              'unified-delay': True, 'tcp-concurrent': True,
              'find-process-mode': 'off', 'tun': {'enable': False},
              'dns': {'enable': False, 'listen': '', 'enhanced-mode': 'redir-host',
                      'ipv6': False, 'cache-algorithm': 'lru', 'cache-max-size': 256,
                      'respect-rules': False},
              'profile': {'store-selected': False, 'store-fake-ip': False},
              'rules': [f'DOMAIN-SUFFIX,{domain},DIRECT' for domain in DOMESTIC_DOMAINS] +
                       ['IP-CIDR,127.0.0.0/8,DIRECT,no-resolve',
                        'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
                        'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve',
                        'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
                        'IP-CIDR6,::1/128,DIRECT,no-resolve', 'MATCH,DDM']}
    config['proxy-providers'] = {'subscription': {'type': 'file', 'path': 'nodes.txt',
                                                  'health-check': {'enable': False}}}
    config['proxy-groups'] = [{'name': 'DDM', 'type': 'select', 'use': ['subscription']}]
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
        data = json.loads((Path(root) / 'preferences.json').read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return Options()
        mode = data.pop('mode', None)
        data.pop('address', None)
        if mode is not None:
            data['enabled'] = mode == 'managed'
        return Options(**data)
    except (OSError, ValueError, TypeError):
        return Options()
