import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { resolve, relative, dirname } from 'node:path'
import { runInNewContext } from 'node:vm'

const root = resolve(import.meta.dirname, '..')
const read = path => readFileSync(resolve(root, path), 'utf8')
const plugins = JSON.parse(read('docs/data/plugins.json'))
const dist = resolve(root, 'docs/.vitepress/dist')
const base = '/DD_Monitor_Plugins/'

// Execute the component's actual setup with browser/lifecycle boundaries mocked.
function mountShowcase({ reduced = false, intersection = true } = {}) {
  const media = { matches: reduced }
  const animations = []
  const target = { animate: (...args) => animations.push(args) }
  const observed = new Set()
  let notify, mount, unmount, disconnected = false
  class Observer {
    constructor(callback) { notify = callback }
    observe(element) { observed.add(element) }
    unobserve(element) { observed.delete(element) }
    disconnect() { disconnected = true }
  }
  const script = read('docs/.vitepress/theme/HomePage.vue').split('<script setup>')[1]
    .split('</script>')[0].replace(/^import .*$/gm, '')
  runInNewContext(script, {
    plugins,
    release: JSON.parse(read('docs/data/software-release.json')),
    ref: value => ({ value: value === null ? { querySelectorAll: () => [target] } : value }),
    onMounted: callback => { mount = callback },
    onUnmounted: callback => { unmount = callback },
    window: { matchMedia: () => media, ...(intersection ? { IntersectionObserver: Observer } : {}) },
    IntersectionObserver: Observer
  })
  mount()
  return { media, animations, observed, unmount,
    visible: () => notify?.([{ target, isIntersecting: true }]),
    get disconnected() { return disconnected }
  }
}

test('reduced motion and unsupported browsers leave the static showcase usable', () => {
  for (const options of [{ reduced: true }, { intersection: false }]) {
    const page = mountShowcase(options)
    assert.equal(page.observed.size, 0)
    assert.equal(page.animations.length, 0)
    page.unmount()
  }
})

test('scroll entrance releases its observer and respects a changed motion preference', () => {
  const page = mountShowcase()
  assert.equal(page.observed.size, 1)
  page.visible()
  assert.equal(page.animations.length, 1)
  assert.equal(page.observed.size, 0)
  assert.equal(page.animations[0][0].at(-1).opacity, 1)
  page.unmount()
  assert.ok(page.disconnected)
  const changed = mountShowcase()
  changed.media.matches = true
  changed.visible()
  assert.equal(changed.animations.length, 0)
  assert.equal(changed.observed.size, 0)
  changed.unmount()
})
const walk = dir => readdirSync(dir, { withFileTypes: true }).flatMap(item => {
  const path = resolve(dir, item.name)
  return item.isDirectory() ? walk(path) : [path]
})

test('catalog identities agree with plugin manifests and published versions are explicit', () => {
  assert.equal(plugins.length, 3)
  assert.equal(new Set(plugins.map(p => p.id)).size, 3)
  for (const p of plugins) {
    const manifest = JSON.parse(read(`plugins/${p.id}/plugin.json`))
    assert.equal(p.name, manifest.name)
    // Published artifacts can be newer than this checkout (e.g. match_sync).
    assert.match(p.version, /^\d+\.\d+(?:\.\d+)?$/)
    assert.equal(p.minAppVersion, '0.3')
    assert.ok(existsSync(resolve(root, `docs/plugins/${p.id}.md`)))
    assert.equal(p.icons.length, p.platforms.length)
    for (const icon of p.icons) assert.ok(existsSync(resolve(root, `docs/public/platforms/${icon}`)))
  }
})

test('downloads pin separate releases, correct package names and SHA256 fingerprints', () => {
  for (const p of plugins) {
    const url = new URL(p.download)
    assert.equal(url.origin, 'https://github.com')
    assert.ok(url.pathname.startsWith('/Lanpropro/DD_Monitor_Plugins/releases/download/'))
    assert.equal(url.pathname.split('/').at(-1), `${p.id}-${p.version}.zip`)
    assert.ok(!url.pathname.includes('/latest/'))
    assert.match(p.sha256, /^[a-f0-9]{64}$/)
  }
})

test('built pages preserve compatibility boundaries and real downloads', () => {
  for (const page of ['index.html', 'software.html', 'download.html', 'guide.html', 'plugins/index.html', 'plugins/domestic_live.html', 'plugins/global_live.html', 'plugins/match_sync.html']) {
    assert.ok(existsSync(resolve(dist, page)), `missing page: ${page}; run npm run docs:build first`)
  }
  const download = readFileSync(resolve(dist, 'download.html'), 'utf8')
  const release = JSON.parse(read('docs/data/software-release.json'))
  assert.ok(download.includes(release.download))
  assert.ok(download.includes(release.release))
  assert.equal(release.tag, 'v0.4')
  assert.ok(download.includes(release.sha256))
  for (const module of release.legacyPackage.packageInspection.missing_modules) assert.ok(download.includes(module))
  const catalog = readFileSync(resolve(dist, 'plugins/index.html'), 'utf8')
  for (const p of plugins) assert.ok(catalog.includes(p.download))
  assert.ok(catalog.includes('Source code ZIP'))
})

test('homepage exposes software and every plugin download before JavaScript runs', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.ok(home.includes('href="/DD_Monitor_Plugins/download"'))
  assert.ok(home.includes('id="plugins"'))
  assert.ok(home.includes('screenshots/landscape.webp'))
  const release = JSON.parse(read('docs/data/software-release.json'))
  assert.ok(home.includes(release.download))
  assert.ok(home.includes(`${release.tag} 已发布`))
  for (const plugin of plugins) {
    assert.ok(home.includes(plugin.download), `missing homepage download: ${plugin.id}`)
    assert.ok(home.includes(plugin.name), `missing homepage plugin: ${plugin.id}`)
    assert.ok(home.includes(`/DD_Monitor_Plugins/plugins/${plugin.id}`))
  }
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
