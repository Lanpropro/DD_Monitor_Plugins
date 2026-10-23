import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { inflateRawSync } from 'node:zlib'
import { resolve, relative, dirname } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const read = path => readFileSync(resolve(root, path), 'utf8')
const plugins = JSON.parse(read('docs/data/plugins.json'))
const dist = resolve(root, 'docs/.vitepress/dist')
const base = '/DD_Monitor_Plugins/'
const walk = dir => readdirSync(dir, { withFileTypes: true }).flatMap(item => {
  const path = resolve(dir, item.name)
  return item.isDirectory() ? walk(path) : [path]
})

// Source packages and already published releases have independent versions.
const publishedZip = p => readFileSync(resolve(root, `dist/published/${p.id}-${p.version}.zip`))
function zipEntry(bytes, name) {
  let end = bytes.length - 22
  while (end >= 0 && bytes.readUInt32LE(end) !== 0x06054b50) end--
  assert.ok(end >= 0, 'ZIP directory missing')
  const count = bytes.readUInt16LE(end + 10)
  let offset = bytes.readUInt32LE(end + 16)
  for (let index = 0; index < count; index++) {
    assert.equal(bytes.readUInt32LE(offset), 0x02014b50)
    const method = bytes.readUInt16LE(offset + 10)
    const size = bytes.readUInt32LE(offset + 20)
    const nameLength = bytes.readUInt16LE(offset + 28)
    const extraLength = bytes.readUInt16LE(offset + 30)
    const commentLength = bytes.readUInt16LE(offset + 32)
    const entryName = bytes.subarray(offset + 46, offset + 46 + nameLength).toString('utf8')
    if (entryName === name) {
      const local = bytes.readUInt32LE(offset + 42)
      const start = local + 30 + bytes.readUInt16LE(local + 26) + bytes.readUInt16LE(local + 28)
      const data = bytes.subarray(start, start + size)
      assert.ok(method === 0 || method === 8, 'Unsupported ZIP compression')
      return method === 8 ? inflateRawSync(data) : data
    }
    offset += 46 + nameLength + extraLength + commentLength
  }
  assert.fail(`Missing ZIP entry: ${name}`)
}

test('published catalog is a unique subset of source IDs with actual release manifests', () => {
  assert.ok(plugins.length > 0)
  assert.equal(new Set(plugins.map(p => p.id)).size, plugins.length)
  const sourceIds = readdirSync(resolve(root, 'plugins')).filter(id => existsSync(resolve(root, `plugins/${id}/plugin.json`)))
  for (const p of plugins) {
    assert.ok(sourceIds.includes(p.id))
    const manifest = JSON.parse(zipEntry(publishedZip(p), `${p.id}/plugin.json`).toString('utf8'))
    assert.equal(p.id, manifest.id)
    assert.equal(p.name, manifest.name)
    assert.equal(p.version, manifest.version)
    assert.equal(p.download, `https://github.com/Lanpropro/DD_Monitor_Plugins/releases/download/v1.0/${p.id}-${p.version}.zip`)
    assert.ok(existsSync(resolve(root, `docs/plugins/${p.id}.md`)))
    assert.equal(p.icons.length, p.platforms.length)
    for (const icon of p.icons) assert.ok(existsSync(resolve(root, `docs/public/platforms/${icon}`)))
  }
})

test('published package checksums match the actual versioned release ZIP files', () => {
  for (const p of plugins) {
    const bytes = publishedZip(p)
    assert.equal(createHash('sha256').update(bytes).digest('hex'), p.sha256)
  }
})

test('built pages preserve compatibility boundaries and real downloads', () => {
  for (const page of ['index.html', 'software.html', 'download.html', 'guide.html', 'plugins/index.html', 'plugins/domestic_live.html', 'plugins/global_live.html', 'plugins/match_sync.html']) {
    assert.ok(existsSync(resolve(dist, page)), `missing page: ${page}; run npm run docs:build first`)
  }
  const download = readFileSync(resolve(dist, 'download.html'), 'utf8')
  const release = JSON.parse(read('docs/data/software-release.json'))
  assert.ok(download.replace(/<[^>]+>/g, '').includes('公开 v0.2 EXE 安装包缺少插件所需模块'))
  assert.ok(download.includes(release.download))
  assert.ok(download.includes(release.sha256))
  for (const module of release.packageInspection.missing_modules) assert.ok(download.includes(module))
  const catalog = readFileSync(resolve(dist, 'plugins/index.html'), 'utf8')
  for (const p of plugins) assert.ok(catalog.includes(p.download))
  assert.ok(catalog.includes('Source code ZIP'))
})

test('every generated internal link, anchor and local asset resolves under the Pages base', () => {
  for (const path of walk(dist).filter(p => p.endsWith('.html'))) {
    const html = readFileSync(path, 'utf8')
    for (const [, attr, value] of html.matchAll(/\b(href|src)="([^"]+)"/g)) {
      const url = value.replaceAll('&amp;', '&')
      if (/^(https?:|data:|mailto:|tel:)/.test(url) || !url) continue
      assert.ok(!url.startsWith('/') || url.startsWith(base), `${relative(dist, path)}: wrong base ${url}`)
      const [pathname, fragment] = url.split('#')
      const withoutQuery = decodeURIComponent(pathname.split('?')[0])
      let target = !withoutQuery ? path : withoutQuery.startsWith(base)
        ? resolve(dist, withoutQuery.slice(base.length))
        : resolve(dirname(path), withoutQuery)
      if (withoutQuery.endsWith('/')) target = resolve(target, 'index.html')
      else if (!existsSync(target) && !target.endsWith('.html')) target += '.html'
      assert.ok(existsSync(target), `${relative(dist, path)}: missing ${attr} ${url}`)
      if (fragment && target.endsWith('.html')) {
        const targetHtml = readFileSync(target, 'utf8')
        assert.ok(targetHtml.includes(`id="${decodeURIComponent(fragment)}"`), `${relative(dist, path)}: missing anchor ${url}`)
      }
    }
  }
})
