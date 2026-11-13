"""Retain profile DNS and host resolution without importing its listeners or routing."""
import json
import re
import ipaddress
import logging


DNS_KEYS = {'enable', 'ipv6', 'ipv6-timeout', 'use-hosts', 'use-system-hosts',
            'default-nameserver', 'nameserver', 'proxy-server-nameserver',
            'nameserver-policy', 'proxy-server-nameserver-policy',
            'direct-nameserver', 'direct-nameserver-follow-policy', 'prefer-h3'}


def _trace_format(kind, document=None):
    logger = logging.getLogger('ddm.proxy_control')
    if not isinstance(document, dict) or not isinstance(document.get('proxies'), list):
        logger.info('proxy profile format=%s', kind)
        return
    records = document['proxies']
    counts = {'ipv4': 0, 'ipv6': 0, 'domain': 0, 'missing': 0}
    for record in records:
        server = record.get('server') if isinstance(record, dict) else None
        if not isinstance(server, str) or not server:
            counts['missing'] += 1
            continue
        try:
            address = ipaddress.ip_address(server.strip('[]'))
            counts['ipv4' if address.version == 4 else 'ipv6'] += 1
        except ValueError:
            counts['domain'] += 1
    logger.info('proxy profile format=%s nodes=%d server_kinds=%s', kind, len(records), counts)


def merge_resolution(config, profile):
    try:
        text = profile.decode('utf-8-sig')
    except UnicodeError:
        return 0, 0
    if ':' not in text:
        _trace_format('encoded-or-scalar')
        return 0, 0
    if re.match(r'^\s*[a-zA-Z][a-zA-Z0-9+.-]*://', text):
        _trace_format('uri')
        return 0, 0  # URI/Base64 parsing stays in Mihomo; no YAML import needed.
    try:
        document = json.loads(text)
        kind = 'json'
    except ValueError:
        # Import only for YAML sessions; no host dependency or background parser.
        from .vendor import yaml
        try:
            document = yaml.safe_load(text)
            kind = 'yaml'
        except yaml.YAMLError:
            _trace_format('provider-only')
            return 0, 0
    _trace_format(kind, document)
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
    if type(document.get('ipv6')) is bool:
        config['ipv6'] = document['ipv6']
        if 'ipv6' not in retained:
            config['dns']['ipv6'] = document['ipv6']
    # An omitted node resolver follows the profile's explicitly supplied main resolver.
    if 'nameserver' in retained and 'proxy-server-nameserver' not in retained:
        config['dns']['proxy-server-nameserver'] = retained['nameserver']
    return len(retained), len(hosts)
