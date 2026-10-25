"""Validate untrusted submission bytes; never extract or execute plugin code."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile

FIELDS = {'插件 ID': 'id', '插件名称': 'name', '版本': 'version', '简介': 'description',
          '源码仓库': 'source', '安装包 ZIP': 'download', '兼容要求': 'requirements',
          '权限与网络访问': 'permissions'}
MAX_PACKAGE = 32 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024
MARKER = '<!-- plugin-submission-check -->'
BASE = 'develop'


class InvalidSubmission(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidSubmission(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def parse_submission(body):
    require(isinstance(body, str) and len(body) <= 20000, '投稿内容为空或过长。')
    sections = re.split(r'^### (.+)$', body.replace('\r\n', '\n'), flags=re.MULTILINE)
    values = {}
    for label, text in zip(sections[1::2], sections[2::2]):
        require(label not in values, '表单包含重复字段。')
        values[label] = text.strip()
    result = {}
    for label, key in FIELDS.items():
        value = values.get(label, '')
        require(value and value != '_No response_' and len(value) <= 3000, '请完整填写全部投稿字段。')
        result[key] = value
    require(re.fullmatch(r'[a-z][a-z0-9_]{0,63}', result['id']), '插件 ID 格式不正确。')
    require(re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,3}', result['version']), '版本必须由数字和点组成。')
    require(len(result['name']) <= 100 and '\n' not in result['name'], '插件名称过长或包含换行。')
    try:
        source = urlsplit(result['source'])
        package = urlsplit(result['download'])
    except ValueError as error:
        raise InvalidSubmission('源码或安装包地址格式不正确。') from error
    require(source.scheme == 'https' and source.netloc == 'github.com' and not source.query and not source.fragment,
            '源码必须是公开 GitHub 仓库的 HTTPS 地址。')
    require(re.fullmatch(r'/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', source.path)
            and source.path.rsplit('/', 1)[-1] not in ('.', '..'), '源码地址应只包含仓库所有者和仓库名。')
    require(package.scheme == 'https' and package.netloc == 'github.com'
            and not package.query and not package.fragment, '安装包必须是 GitHub Release 的 HTTPS 附件直链。')
    prefix = source.path + '/releases/download/'
    require(package.path.startswith(prefix), '安装包必须来自所填源码仓库的 Release。')
    remainder = package.path[len(prefix):].split('/')
    require(len(remainder) == 2 and all(remainder) and unquote(remainder[-1]).lower().endswith('.zip'),
            '请填写单插件 ZIP 的 Release 附件地址。')
    return result


def safe_download_url(url):
    parts = urlsplit(url)
    require(parts.scheme == 'https' and parts.netloc in (
        'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'),
        '下载跳转到了不允许的地址。')


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        safe_download_url(url)
        return super().redirect_request(request, fp, code, message, headers, url)


def download_package(url):
    safe_download_url(url)
    # This opener has no token, cookies, netrc or environment proxy credentials.
    opener = build_opener(ProxyHandler({}), ReleaseRedirect())
    started = time.monotonic()
    with opener.open(Request(url, headers={'User-Agent': 'DDM-plugin-submissions'}), timeout=20) as response:
        data = bytearray()
        while chunk := response.read(64 * 1024):
            data.extend(chunk)
            require(len(data) <= MAX_PACKAGE, 'ZIP 超过 32 MiB。')
            require(time.monotonic() - started < 90, '下载耗时超过限制。')
    require(data, '下载的安装包为空。')
    return bytes(data)


def inspect_package(data, submission):
    require(0 < len(data) <= MAX_PACKAGE, 'ZIP 为空或超过 32 MiB。')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            require(0 < len(files) <= 256, 'ZIP 文件数超过 256 个。')
            require(sum(info.file_size for info in files) <= MAX_UNPACKED, 'ZIP 解压大小超过 100 MiB。')
            seen = set()
            for info in files:
                name = info.filename
                parts = name.rstrip('/').split('/')
                require(info.orig_filename == name and '\\' not in name and ':' not in name
                        and not any(p in ('', '.', '..') for p in parts)
                        and parts[0] == submission['id'] and (info.is_dir() or len(parts) >= 2),
                        'ZIP 包含不安全路径或错误的顶层目录。')
                require(all(len(p) <= 255 and not p.endswith((' ', '.'))
                            and not re.search(r'[<>"|?*\x00-\x1f]', p)
                            and not re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
                            for p in parts), 'ZIP 包含 Windows 不支持的文件名。')
                key = name.rstrip('/').casefold()
                require(key not in seen, 'ZIP 包含重复或大小写冲突的路径。')
                seen.add(key)
                kind = stat.S_IFMT(info.external_attr >> 16)
                require(kind in (0, stat.S_IFREG, stat.S_IFDIR), 'ZIP 不允许符号链接或特殊文件。')
                require(not info.flag_bits & 1 and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                        'ZIP 不支持加密或此压缩方式。')
            regular = {info.filename.casefold() for info in files if not info.is_dir()}
            require(not any('/'.join(info.filename.casefold().split('/')[:i]) in regular
                            for info in files for i in range(1, len(info.filename.rstrip('/').split('/')))),
                    'ZIP 的文件与目录路径冲突。')
            prefix = submission['id'] + '/'
            manifest_info = archive.getinfo(prefix + 'plugin.json')
            archive.getinfo(prefix + 'plugin.py')
            require(manifest_info.file_size <= 65536, 'plugin.json 超过 64 KiB。')
            manifest = json.loads(archive.read(manifest_info).decode('utf-8'))
            require(isinstance(manifest, dict), 'plugin.json 必须是 JSON 对象。')
            for key in ('id', 'name', 'version'):
                require(manifest.get(key) == submission[key], '清单的 ID、名称或版本与表单不一致。')
            require(isinstance(manifest.get('description'), str) and manifest['description'].strip(),
                    '清单缺少 description。')
            require(archive.testzip() is None, 'ZIP 内容校验失败。')
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError, RecursionError, RuntimeError, NotImplementedError) as error:
        raise InvalidSubmission('ZIP 损坏，或缺少有效的 plugin.json / plugin.py。') from error
    return hashlib.sha256(data).hexdigest()


def candidate(issue, registry, official_ids, downloader=None):
    require(issue.get('state') == 'open' and 'pull_request' not in issue, '只能处理仍然打开的投稿 Issue。')
    require(issue.get('title', '').startswith('[插件投稿]'), '此 Issue 不是插件投稿。')
    submission = parse_submission(issue.get('body'))
    require(submission['id'] not in official_ids | {'danmaku_log'}
            and not any(p['id'] == submission['id'] for p in registry['plugins']),
            '插件 ID 已存在；已有插件更新请直接联系维护者。')
    submission['sha256'] = inspect_package((downloader or download_package)(submission['download']), submission)
    submission['issue'] = issue['number']
    submission['author'] = issue['user']['login']
    fingerprint = hashlib.sha256(canonical(submission)).hexdigest()
    return submission, fingerprint


def github_api(repo, token):
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo), '仓库标识无效。')
    # Refuse API redirects so the repository token cannot follow an external URL.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(path, payload=None, method=None):
        request = Request('https://api.github.com/repos/' + repo + path,
            data=canonical(payload) if payload is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'User-Agent': 'DDM-plugin-submissions'}, method=method)
        try:
            with opener.open(request, timeout=20) as response:
                data = response.read(4 * 1024 * 1024 + 1)
                require(len(data) <= 4 * 1024 * 1024, 'GitHub API 返回内容过大。')
                return json.loads(data)
        except HTTPError as error:
            raise InvalidSubmission('GitHub API 请求失败（HTTP ' + str(error.code) + '）。') from error
    return call


def registry_snapshot(api):
    head = api('/git/ref/heads/' + BASE)['object']['sha']
    content = api('/contents/catalog/community.json?ref=' + head)
    registry = json.loads(base64.b64decode(content['content']))
    require(registry.get('schema_version') == 1 and isinstance(registry.get('plugins'), list), '社区索引格式不正确。')
    official = api('/contents/plugins?ref=' + head)
    return head, content['sha'], registry, {entry['name'] for entry in official if entry['type'] == 'dir'}


def report(api, number, text):
    body = MARKER + '\n' + text
    comments = api(f'/issues/{number}/comments?per_page=100')
    previous = next((c for c in comments if c['user']['login'] == 'github-actions[bot]'
                     and c.get('body', '').startswith(MARKER)), None)
    if previous:
        api('/issues/comments/' + str(previous['id']), {'body': body}, 'PATCH')
    else:
        api(f'/issues/{number}/comments', {'body': body})


def approve(api, repo, actor, number, expected):
    require(re.fullmatch(r'[0-9a-f]{64}', expected), '请复制最近一次检查的完整指纹。')
    permission = api('/collaborators/' + quote(actor, safe='') + '/permission')['permission']
    require(permission in ('admin', 'maintain', 'write'), '只有仓库维护者可以发起上架。')
    branch = f'function/plugin-submission-{number}-{expected[:12]}'
    existing = api('/pulls?state=all&head=' + quote(repo.split('/')[0] + ':' + branch, safe=''))
    if existing:
        return existing[0]['html_url']
    issue = api('/issues/' + str(number))
    head, file_sha, registry, official = registry_snapshot(api)
    entry, fingerprint = candidate(issue, registry, official)
    require(fingerprint == expected, '投稿内容或 ZIP 已变化，请重新检查并审核，不能使用旧指纹。')
    registry['plugins'].append(entry)
    registry['plugins'].sort(key=lambda p: p['id'])
    refs = api('/git/matching-refs/heads/' + branch)
    if any(ref['ref'] == 'refs/heads/' + branch for ref in refs):
        file_sha = api('/contents/catalog/community.json?ref=' + branch)['sha']
    else:
        api('/git/refs', {'ref': 'refs/heads/' + branch, 'sha': head})
    api('/contents/catalog/community.json', {
        'message': 'feat: register community plugin ' + entry['id'], 'branch': branch, 'sha': file_sha,
        'content': base64.b64encode((json.dumps(registry, ensure_ascii=False, indent=2) + '\n').encode('utf-8')).decode('ascii')}, 'PUT')
    pr = api('/pulls', {'title': 'feat: register community plugin ' + entry['id'], 'base': BASE, 'head': branch,
        'body': f"投稿 #{number}，由 @{actor} 复核后生成。\n\n"
                f"检查指纹：`{fingerprint}`\n\n安装包 SHA256：`{entry['sha256']}`\n\n"
                '仅更新社区索引；未导入或执行插件代码，不自动合并、不发布安装包、不部署网站。\n'
                '维护者仍需检查源码、许可证、网络访问与权限，并完成实际安装验收。'})
    return pr['html_url']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'approve'))
    args = parser.parse_args()
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    if args.mode == 'check' and not event.get('issue', {}).get('title', '').startswith('[插件投稿]'):
        return
    api = github_api(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'])
    if args.mode == 'approve':
        number = os.environ.get('ISSUE_NUMBER', '')
        require(re.fullmatch(r'[1-9][0-9]{0,9}', number), '投稿编号必须是正整数。')
        print(approve(api, os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_ACTOR'],
                      int(number), os.environ.get('EXPECTED_FINGERPRINT', '')))
        return
    issue = event['issue']
    try:
        _, _, registry, official = registry_snapshot(api)
        entry, fingerprint = candidate(issue, registry, official)
    except (InvalidSubmission, OSError) as error:
        report(api, issue['number'], '基础检查未通过，请修正表单或安装包后重新编辑投稿。\n\n' +
               (str(error) if isinstance(error, InvalidSubmission) else '下载失败，请检查 Release 附件是否公开。'))
        raise
    report(api, issue['number'], '基础检查通过，等待维护者人工审核（不等于安全认证）。\n\n'
        f"插件 ID：`{entry['id']}`\n\n安装包 SHA256：`{entry['sha256']}`\n\n"
        f'检查指纹：`{fingerprint}`\n\n'
        '维护者审核后可运行 Plugin submissions 工作流，填写投稿编号和以上完整指纹，生成面向 develop 的索引 PR。')


if __name__ == '__main__':
    try:
        main()
    except (InvalidSubmission, OSError) as error:
        print(str(error) if isinstance(error, InvalidSubmission) else '网络或文件访问失败。', file=sys.stderr)
        sys.exit(1)
