import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { resolve, relative, dirname } from 'node:path'
import { runInNewContext } from 'node:vm'
import { createAppearanceToggle } from '../docs/.vitepress/theme/appearance.mjs'

const root = resolve(import.meta.dirname, '..')
const read = path => readFileSync(resolve(root, path), 'utf8')
const plugins = JSON.parse(read('docs/data/plugins.json'))
const dist = resolve(root, 'docs/.vitepress/dist')
const base = '/DD_Monitor_Plugins/'

// Execute the component's actual setup with browser/lifecycle boundaries mocked.
function mountShowcase({ reduced = false, intersection = true } = {}) {
  const media = { matches: reduced, addEventListener() {}, removeEventListener() {} }
  const animations = []
  const classes = new Set()
  const target = { animate: (...args) => animations.push(args), classList: {
    add: name => classes.add(name), remove: name => classes.delete(name)
  } }
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
  return { media, animations, observed, classes, unmount,
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
  assert.ok(page.classes.has('reveal-pending'), 'content waits until it enters the viewing area')
  page.visible()
  assert.equal(page.classes.size, 0)
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
  assert.ok(!home.includes('查看完整截图'), 'the presentation should not show a redundant screenshot link')
  assert.ok(home.includes('aria-label="暂停截图轮播"'), 'autoplay must have a manual pause control')
  for (const label of ['横屏观看', '弹幕布局', '竖屏布局']) assert.ok(home.includes(label))
  const release = JSON.parse(read('docs/data/software-release.json'))
  assert.ok(home.includes(release.download))
  assert.ok(home.includes(`${release.tag} 已发布`))
  for (const plugin of plugins) {
    assert.ok(home.includes(plugin.download), `missing homepage download: ${plugin.id}`)
    assert.ok(home.includes(plugin.name), `missing homepage plugin: ${plugin.id}`)
    assert.ok(home.includes(`/DD_Monitor_Plugins/plugins/${plugin.id}`))
  }
})

test('Dock navigation renders all destinations and exactly one current page on every route', () => {
  const routes = ['index', 'software', 'download', 'guide', 'plugins/index', 'plugins/domestic_live', 'plugins/global_live', 'plugins/match_sync']
  for (const route of routes) {
    const html = readFileSync(resolve(dist, route + '.html'), 'utf8')
    const dock = html.match(/<nav\b[^>]*class="dock-nav"[^>]*>[\s\S]*?<\/nav>/)?.[0]
    assert.ok(dock, `missing Dock: ${route}`)
    const header = html.match(/<header\b[\s\S]*?<\/header>/)?.[0]
    assert.ok(header?.includes(dock), `Dock must be inside the top header: ${route}`)
    assert.equal((dock.match(/class="dock-label"/g) || []).length, 5, `every icon needs a readable label: ${route}`)
    assert.equal((dock.match(/aria-hidden="true"/g) || []).length, 5, `decorative icons must not repeat link names: ${route}`)
    // Narrow screens hide the labels, so each link carries the name itself.
    const links = dock.match(/<a\b[^>]*class="dock-item"[^>]*>/g) || []
    assert.equal(links.length, 5, `every destination needs its own link: ${route}`)
    for (const link of links) assert.match(link, /aria-label="[^"]+"/, `dock link needs an accessible name: ${route}`)
    for (const target of ['', 'software', 'plugins/', 'download', 'guide']) assert.ok(dock.includes(`href="${base}${target}"`))
    assert.equal((dock.match(/aria-current="page"/g) || []).length, 1, route)
    const expected = route.startsWith('plugins/') ? 'plugins/' : route === 'index' ? '' : route
    assert.match(dock, new RegExp(`href="${base}${expected}"[^>]*aria-current="page"`), route)
  }
})

test('opening screen shows the placeholder wall before any page content', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  const wall = home.match(/<section class="splash"[^>]*>/)
  assert.ok(wall, 'missing opening wall')
  assert.match(wall[0], /aria-hidden="true"/, 'the decorative wall must stay out of the accessibility tree')
  const wallTiles = (home.match(/class="splash-tile"/g) || []).length
  assert.equal(wallTiles, 224, 'the wall repeats its 56 tiles twice per axis so it wraps without a seam')
  assert.ok(home.includes('拖动查看'), 'the opening screen must say it can be dragged')
  assert.ok(home.indexOf('class="splash"') < home.indexOf('class="showcase"'), 'the wall comes before the page content')
})

test('the full-screen wall leaves vertical touch gestures to page scrolling', () => {
  const splash = read('docs/.vitepress/theme/SplashWall.vue')
  // touch-action:none 会让手机停在开屏墙，而顶栏此时是隐藏的，等于没有导航入口。
  assert.match(splash, /touch-action:\s*pan-y/, 'vertical swipes must scroll the page')
  assert.ok(!/touch-action:\s*none/.test(splash), 'the wall must not swallow touch scrolling')
  // 触摸拖动要等横向位移超过阈值才接管，否则纵向手势会被拖墙抢走。
  assert.match(splash, /pendingDrag/, 'touch drags need a pending state')
  assert.match(splash, /dx\s*<\s*dy/, 'a mostly vertical gesture must not become a wall drag')
})

test('the plugin catalog escapes the narrow documentation column', () => {
  const catalogPage = read('docs/plugins/index.md')
  assert.match(catalogPage, /layout:\s*page/, 'the catalog needs the full-width page layout')
  const css = read('docs/.vitepress/theme/style.css')
  assert.match(css, /\.catalog-page\s*\{[^}]*max-width:\s*\d+px/, 'the catalog needs its own width cap')
  // 文档三栏布局下的卡片只有 335px，图标、版本和按钮会挤在一起。
  const baseGrid = css.match(/(?:^|\n)\.plugin-grid\s*\{([^}]*)\}/)
  assert.ok(baseGrid, 'missing the shared plugin grid')
  assert.doesNotMatch(baseGrid[1], /repeat\(2/, 'the shared grid must not stay at the old two columns')
  assert.match(baseGrid[1], /repeat\(3/, 'wide screens fit three cards')
  assert.match(css, /@media \(max-width: 760px\)[^@]*\.catalog-page \.plugin-grid\s*\{[^}]*grid-template-columns:\s*1fr/,
    'narrow screens fall back to one column')
})

test('plugin detail pages list every published version they can prove', () => {
  for (const p of plugins) {
    const page = read(`docs/plugins/${p.id}.md`)
    const history = page.split('## 更新记录')[1]
    assert.ok(history, `missing changelog: ${p.id}`)
    assert.ok(history.includes(`**${p.version}`), `changelog must name the published version ${p.version}: ${p.id}`)
  }
})

test('real software thumbnails and per-plugin instructions remain readable before hydration', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.ok(!home.includes('交互示意'))
  assert.ok(!home.includes('aria-label="主画面比例"'))
  assert.ok(home.includes('aria-label="观看体验截图"'))
  const gallery = home.match(/<div class="feature-gallery"[\s\S]*?<\/figure>/)?.[0]
  assert.ok(gallery)
  assert.equal((gallery.match(/class="feature-thumbnail"/g) || []).length, 3)
  for (const file of ['landscape.webp', 'danmaku.webp', 'portrait.webp']) {
    assert.ok(gallery.includes(`/screenshots/${file}`), `missing real thumbnail: ${file}`)
  }
  assert.equal((gallery.match(/aria-pressed="true"/g) || []).length, 1)
  assert.ok(gallery.includes('aria-controls="feature-screenshot"'))
  assert.ok(home.includes('id="plugin-demo"'))
  for (const plugin of plugins) {
    assert.equal(plugin.steps.length, 3)
    assert.equal(new Set(plugin.steps).size, 3)
  }
  for (const step of plugins[0].steps) assert.ok(home.includes(step))
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

function mountGallery({ reduced = false, intersection = true, resize = true } = {}) {
  const timers = new Map(), listeners = new Map()
  const media = { matches: reduced, addEventListener() {}, removeEventListener() {} }
  const story = { offsetHeight: 1200, top: 140, getBoundingClientRect() { return { top: this.top } } }
  const pin = { offsetHeight: 600 }
  const product = { offsetHeight: 520 }
  const doc = { hidden: false, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  let mount, unmount, notify, measure, next = 0, refs = 0, disconnected = false, sizeDisconnected = false
  class Observer {
    constructor(callback) { notify = callback }
    observe() {}
    disconnect() { disconnected = true }
  }
  class SizeObserver {
    constructor(callback) { measure = callback }
    observe() { measure() }
    disconnect() { sizeDisconnected = true }
  }
  const script = read('docs/.vitepress/theme/ScreenshotShowcase.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ active, progress, controlsReady, productHeight, enhanced, paused, selectShot, togglePlayback, interact, update, preferenceChanged })', {
    ref: value => ({ value: value === null ? [story, pin, product][refs++] : value }),
    onMounted: fn => { mount = fn }, onUnmounted: fn => { unmount = fn },
    window: { innerHeight: 800, matchMedia: () => media, ...(intersection ? { IntersectionObserver: Observer } : {}), ...(resize ? { ResizeObserver: SizeObserver } : {}), addEventListener() {}, removeEventListener() {} },
    document: doc, IntersectionObserver: Observer, ResizeObserver: SizeObserver, getComputedStyle: () => ({ top: '100px' }),
    setTimeout: fn => { timers.set(++next, fn); return next }, clearTimeout: id => timers.delete(id),
    cancelAnimationFrame() {}, requestAnimationFrame() { return 1 }
  })
  mount()
  return { state, story, doc, media, timers, listeners, unmount,
    visible(value) { notify?.([{ isIntersecting: value }]) },
    resizeProduct(height) { product.offsetHeight = height; measure?.() },
    tick() { const fn = timers.values().next().value; assert.ok(fn, 'expected a scheduled slide'); timers.clear(); fn() },
    get disconnected() { return disconnected },
    get sizeDisconnected() { return sizeDisconnected }
  }
}

test('screenshot scroll contracts the display and playback loops only while being watched', () => {
  const gallery = mountGallery()
  assert.equal(gallery.state.productHeight.value, 520)
  gallery.resizeProduct(680)
  assert.equal(gallery.state.productHeight.value, 680, 'layout follows the real frame height after a ratio change')
  assert.equal(gallery.state.progress.value, 0)
  gallery.visible(true)
  assert.equal(gallery.timers.size, 0, 'no slideshow before the controls are revealed')
  gallery.story.top = -500
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 1)
  for (const expected of [1, 2, 0]) { gallery.tick(); assert.equal(gallery.state.active.value, expected) }
  gallery.state.togglePlayback()
  assert.equal(gallery.timers.size, 0)
  gallery.state.selectShot(2)
  assert.equal(gallery.state.active.value, 2)
  assert.equal(gallery.timers.size, 0, 'manual selection respects pause')
  gallery.state.togglePlayback()
  gallery.state.interact(true)
  assert.equal(gallery.timers.size, 0, 'do not advance beneath a pointer or keyboard focus')
  gallery.state.interact(false)
  assert.equal(gallery.timers.size, 1)
  gallery.doc.hidden = true
  gallery.listeners.get('visibilitychange')()
  assert.equal(gallery.timers.size, 0)
  gallery.doc.hidden = false
  gallery.visible(false)
  assert.equal(gallery.timers.size, 0)
  gallery.visible(true)
  assert.equal(gallery.timers.size, 1)
  gallery.unmount()
  assert.equal(gallery.timers.size, 0)
  assert.equal(gallery.listeners.size, 0)
  assert.ok(gallery.disconnected)
  assert.ok(gallery.sizeDisconnected)
})

test('screenshot contraction starts when the frame center reaches the viewport center', () => {
  const gallery = mountGallery()
  // 800px viewport, 520px frame: its center reaches 400px at a 140px top.
  gallery.story.top = 160
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 0)
  gallery.story.top = 140
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 0)
  gallery.story.top = 98
  gallery.state.update()
  assert.ok(Math.abs(gallery.state.progress.value - .1) < .001)
  gallery.unmount()
})

test('revealed screenshot controls stay visible during a ratio change', () => {
  const gallery = mountGallery()
  gallery.story.top = -500
  gallery.state.update()
  assert.equal(gallery.state.controlsReady.value, true)
  gallery.state.selectShot(2)
  gallery.resizeProduct(800)
  gallery.story.top = -110
  gallery.state.update()
  assert.equal(gallery.state.controlsReady.value, true, 'resizing must not hide or disable the selected controls')
  gallery.story.top = 100
  gallery.state.update()
  assert.equal(gallery.state.controlsReady.value, false, 'returning to the start restores the scroll reveal')
  gallery.unmount()
})

test('screenshot reduced-motion and observer fallback expose manual controls without autoplay', () => {
  for (const options of [{ reduced: true }, { intersection: false }, { resize: false }]) {
    const gallery = mountGallery(options)
    gallery.visible(true)
    assert.equal(gallery.state.enhanced.value, false)
    assert.equal(gallery.state.progress.value, 1)
    assert.equal(gallery.timers.size, 0)
    gallery.state.selectShot(1)
    assert.equal(gallery.state.active.value, 1)
    gallery.unmount()
  }
  const gallery = mountGallery()
  gallery.media.matches = true
  gallery.state.preferenceChanged()
  assert.equal(gallery.state.enhanced.value, false)
  assert.equal(gallery.state.progress.value, 1)
  gallery.unmount()
})

test('theme wipe originates at the button, supports rapid toggles and falls back without motion', async () => {
  const saved = Object.fromEntries(['window', 'document', 'innerWidth', 'innerHeight'].map(key => [key, globalThis[key]]))
  const wipes = [], bounces = []
  let reduced = false, skipped = 0
  const theme = { value: true }
  globalThis.innerWidth = 1000
  globalThis.innerHeight = 800
  globalThis.window = { matchMedia: () => ({ matches: reduced }) }
  globalThis.document = {
    documentElement: { animate: (...args) => wipes.push(args) },
    startViewTransition(update) {
      const updateCallbackDone = Promise.resolve().then(update)
      return { updateCallbackDone, ready: updateCallbackDone, finished: updateCallbackDone, skipTransition() { skipped++ } }
    }
  }
  const event = { currentTarget: { getBoundingClientRect: () => ({ left: 900, top: 20, width: 40, height: 22 }), animate: (...args) => { bounces.push(args); return { cancel() {} } } } }
  try {
    const toggle = createAppearanceToggle(theme, async () => {})
    await Promise.all([toggle(event), toggle(event), toggle(event)])
    assert.equal(theme.value, false, 'three clicks must apply three state changes')
    assert.ok(skipped > 0)
    assert.equal(bounces.length, 3)
    assert.match(wipes[0][0][0].clipPath, /0px at 920px 31px/)
    assert.equal(wipes[0][1].pseudoElement, '::view-transition-new(root)')
    reduced = true
    await toggle(event)
    assert.equal(theme.value, true)
    assert.equal(bounces.length, 3)
    assert.equal(wipes.length, 3)
    reduced = false
    delete document.startViewTransition
    await toggle(event)
    assert.equal(theme.value, false)
    assert.equal(wipes.length, 3)
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete globalThis[key]
      else globalThis[key] = value
    }
  }
})
