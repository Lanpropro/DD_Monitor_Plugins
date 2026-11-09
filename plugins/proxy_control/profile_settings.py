"""Retain profile DNS and host resolution without importing its listeners or routing."""
import json
import re


DNS_KEYS = {'enable', 'ipv6', 'ipv6-timeout', 'use-hosts', 'use-system-hosts',
            'default-nameserver', 'nameserver', 'proxy-server-nameserver',
            'nameserver-policy', 'proxy-server-nameserver-policy',
            'direct-nameserver', 'direct-nameserver-follow-policy', 'prefer-h3'}


def merge_resolution(config, profile):
    try:
        text = profile.decode('utf-8-sig')
    except UnicodeError:
        return 0, 0
    if ':' not in text or re.match(r'^\s*[a-zA-Z][a-zA-Z0-9+.-]*://', text):
        return 0, 0  # URI/Base64 parsing stays in Mihomo; no YAML import needed.
    try:
        document = json.loads(text)
    except ValueError:
        # Import only for YAML sessions; no host dependency or background parser.
        from .vendor import yaml
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError:
            return 0, 0
    if not isinstance(document, dict):
        return 0, 0
    dns, hosts = document.get('dns') or {}, document.get('hosts') or {}
    if not isinstance(dns, dict) or not isinstance(hosts, dict):
        raise ValueError('订阅的 DNS／hosts 配置格式无效')
    retained = {key: value for key, value in dns.items() if key in DNS_KEYS}
    try:
        json.dumps((retained, hosts))
    except (ValueError, TypeError, RecursionError):
        raise ValueError('订阅的 DNS／hosts 配置格式无效') from None
    if hosts:
        config.setdefault('hosts', {}).update(hosts)
    config['dns'].update(retained)
    # An omitted node resolver should follow the profile's main resolver, not
    # override it with our fallback public servers.
    if 'nameserver' in retained and 'proxy-server-nameserver' not in retained:
        config['dns']['proxy-server-nameserver'] = retained['nameserver']
    return len(retained), len(hosts)
