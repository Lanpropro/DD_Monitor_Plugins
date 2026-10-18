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

test('appearance miniature and editable controls remain usable before hydration', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.ok(home.includes('可操作缩略预览'))
  assert.ok(home.includes('aria-label="软件外观缩略预览"'))
  assert.ok(home.includes('class="mini-sidebar"'))
  assert.equal((home.match(/class="[^"]*\bmini-tile\b[^"]*"/g) || []).length, 6)
  assert.ok(!home.includes('aria-label="主画面比例"'))
  assert.ok(!home.includes('role="separator"'))
  assert.ok(!home.includes('比例支持拖动'))
  assert.ok(home.includes('aria-label="布局预设页面"'))
  const miniature = home.match(/<div class="software-miniature[\s\S]*?<p class="demo-status"/)?.[0]
  assert.ok(miniature?.includes('id="mini-layout-presets"'), 'the preset page belongs inside the software preview')
  assert.ok(!home.includes('<details id="mini-layout-presets"'))
  assert.ok(home.includes('切换布局预设'))
  assert.ok(home.includes('aria-controls="mini-layout-presets"'))
  assert.ok(home.includes('aria-label="选择演示主播 A"'))
  assert.ok(!home.includes('观看组合'))
  assert.ok(home.includes('id="plugin-demo"'))
  for (const plugin of plugins) {
    assert.equal(plugin.steps.length, 3)
    assert.equal(new Set(plugin.steps).size, 3)
  }
  for (const chapter of ['选择主画面','框选计时区域','自动对齐','混合解说','合并弹幕']) assert.ok(home.includes(chapter))
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
  assert.equal(gallery.state.productHeight.value, 680, 'layout follows the real frame height after a viewport resize')
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

test('restored screenshot contraction starts at the original sticky offset', () => {
  const gallery = mountGallery()
  // Original gallery uses the sticky top (100px), with a 420px scroll range.
  gallery.story.top = 160
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 0)
  gallery.story.top = 100
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 0)
  gallery.story.top = 58
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


function mountMiniature() {
  let target = null, nullRefs = 0, backFocus = 0
  const script = read('docs/.vitepress/theme/FeatureDemo.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ presets, preset, presetId, columns, slots, cards, selected, dragged, over, light, pickerOpen, selectCard, placeCard, clearSlot, changePreset, onCardDown, onCardMove, onCardUp, cancelCard, openPicker, closePicker })', {
    computed: fn => ({ get value() { return fn() } }),
    nextTick: fn => fn(),
    ref: value => ({ value: value === null && nullRefs++ === 1 ? { contains: element => element.inside, querySelector: () => ({ focus() { backFocus++ } }) } : value }),
    document: { elementFromPoint: () => ({ closest: () => target }) }
  })
  return { state, get backFocus() { return backFocus }, hit(index, inside = true) { target = index === null ? null : { dataset: { miniSlot: String(index) }, inside } } }
}

test('all software presets apply, preserve assigned cards and reject hidden targets', () => {
  const { state: s } = mountMiniature()
  assert.equal(s.presets.length, 8)
  s.selectCard('a'); s.placeCard(0)
  assert.equal(s.slots.value[0].id, 'a')
  assert.equal(s.selected.value, null)
  for (const preset of s.presets) {
    s.pickerOpen.value = true
    s.changePreset(preset.id)
    assert.equal(s.pickerOpen.value, false)
    assert.equal(s.preset.value.id, preset.id)
    const occupied = new Set()
    for (const [row, col, rows, cols] of preset.cells) {
      assert.ok(row + rows <= preset.rows && col + cols <= preset.cols)
      for (let r = row; r < row + rows; r++) for (let c = col; c < col + cols; c++) {
        assert.ok(!occupied.has(`${r}:${c}`), 'preset cells cannot overlap')
        occupied.add(`${r}:${c}`)
      }
    }
    assert.equal(occupied.size, preset.rows * preset.cols)
    assert.equal(s.slots.value[0].id, 'a')
  }
  s.changePreset('1x1'); s.placeCard(5, 'b')
  assert.equal(s.slots.value[5], null)
  s.placeCard(0, 'b'); assert.equal(s.slots.value[0].id, 'b')
  s.clearSlot(0); assert.equal(s.slots.value[0], null)
  s.changePreset('unknown'); assert.equal(s.presetId.value, '1x1')
})

test('follow cards support pointer dragging, tap placement, cancellation and outside drops', () => {
  const { state: s, hit } = mountMiniature()
  const event = (x, y) => ({ pointerType: 'mouse', button: 0, pointerId: 1, clientX: x, clientY: y, currentTarget: { setPointerCapture() {}, releasePointerCapture() {} } })
  s.onCardDown(event(10,10), s.cards[0]); s.onCardMove(event(12,12))
  assert.equal(s.dragged.value, null, 'small movement remains a tap')
  s.onCardUp(event(12,12)); assert.equal(s.selected.value, 'a')
  s.placeCard(0); assert.equal(s.slots.value[0].id, 'a')
  s.onCardDown(event(10,10), s.cards[1]); hit(2)
  s.onCardMove(event(90,90)); assert.equal(s.over.value, 2)
  s.onCardUp(event(90,90)); assert.equal(s.slots.value[2].id, 'b')
  assert.equal(s.dragged.value, null)
  for (const destination of [[null, true], [3, false]]) {
    s.onCardDown(event(10,10), s.cards[2]); hit(...destination)
    s.onCardMove(event(90,90)); assert.equal(s.over.value, -1)
    s.onCardUp(event(90,90)); assert.equal(s.slots.value[3], null)
  }
  s.onCardDown(event(10,10), s.cards[2]); hit(1); s.onCardMove(event(90,90))
  s.cancelCard(); s.onCardUp(event(90,90))
  assert.equal(s.slots.value[1], null)
  assert.equal(s.dragged.value, null)
})

test('preset secondary page opens inside the preview and returns focus on close or apply', () => {
  const page = mountMiniature(), s = page.state
  let returned = 0
  const trigger = { currentTarget: { focus() { returned++ } } }
  s.selectCard('a'); s.placeCard(0)
  s.openPicker(trigger)
  assert.equal(s.pickerOpen.value, true)
  assert.equal(page.backFocus, 1)
  s.changePreset('2x2')
  assert.equal(s.pickerOpen.value, false)
  assert.equal(returned, 1)
  assert.equal(s.slots.value[0].id, 'a')
  s.openPicker(trigger); s.closePicker()
  assert.equal(s.pickerOpen.value, false)
  assert.equal(returned, 2)
  s.closePicker(); assert.equal(returned, 2, 'closing an already closed page must not move focus')
})

function mountPluginCarousel({ reduced = false, intersection = true, initialIndex = 0 } = {}) {
  const frames = new Map(), timers = new Map(), listeners = new Map()
  let mount, unmount, notify, next = 0, disconnected = 0
  const media = { matches: reduced, addEventListener: (name, fn) => listeners.set('media:' + name, fn), removeEventListener: name => listeners.delete('media:' + name) }
  const group = { offsetWidth: 1008, firstElementChild: { offsetWidth: 320 } }
  const element = { scrollLeft: 0, querySelector: () => group, scrollBy({ left }) { this.scrollLeft += left }, setPointerCapture() {}, releasePointerCapture() {} }
  const doc = { hidden: false, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  class Observer { constructor(fn) { notify = fn } observe() {} disconnect() { disconnected++ } }
  class SizeObserver { observe() {} disconnect() { disconnected++ } }
  const script = read('docs/.vitepress/theme/PluginCarousel.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ paused, sync, normalize, measure, togglePlayback, step, onPointerEnter, onPointerLeave, onFocusIn, onFocusOut, onPointerDown, onPointerMove, onPointerUp, onClickCapture })', {
    defineProps: () => ({ initialIndex }),
    ref: value => ({ value: value === null ? element : value }),
    onMounted: fn => { mount = fn }, onUnmounted: fn => { unmount = fn },
    window: { matchMedia: () => media, ...(intersection ? { IntersectionObserver: Observer } : {}), ResizeObserver: SizeObserver, addEventListener() {}, removeEventListener() {} },
    document: doc, IntersectionObserver: Observer, ResizeObserver: SizeObserver,
    getComputedStyle: () => ({ columnGap: '24px' }),
    requestAnimationFrame: fn => { frames.set(++next, fn); return next }, cancelAnimationFrame: id => frames.delete(id),
    setTimeout: fn => { timers.set(++next, fn); return next }, clearTimeout: id => timers.delete(id)
  })
  mount()
  return { state, element, group, media, doc, frames, timers, listeners, unmount,
    visible(value) { notify?.([{ isIntersecting: value }]) },
    frame(time) { const [id, fn] = frames.entries().next().value; frames.delete(id); fn(time) },
    settle() { const [id, fn] = timers.entries().next().value; timers.delete(id); fn() },
    get disconnected() { return disconnected }
  }
}

test('plugin rail loops seamlessly and keeps its phase after resize', () => {
  const page = mountPluginCarousel(), s = page.state, element = page.element
  assert.equal(element.scrollLeft, 1032, 'start in the middle copy')
  page.visible(true)
  page.frame(100)
  for (let time = 116; time <= 1100; time += 16) page.frame(time)
  assert.ok(element.scrollLeft >= 1058 && element.scrollLeft <= 1061, 'automatic speed is about 28px per second')
  element.scrollLeft = 2064 + 17; s.normalize(); assert.equal(element.scrollLeft, 1032 + 17)
  element.scrollLeft = 1032 - 17; s.normalize(); assert.equal(element.scrollLeft, 2064 - 17)
  element.scrollLeft = 1032 * 1.25; page.group.offsetWidth = 1200; s.measure()
  assert.equal(element.scrollLeft, 1224 * 1.25)
  page.unmount(); assert.equal(page.frames.size, 0); assert.equal(page.disconnected, 2)
  assert.equal(page.listeners.size, 0)
})

test('plugin rail pauses for reading, manual navigation, background and reduced motion', () => {
  const page = mountPluginCarousel(), s = page.state
  page.visible(true); assert.equal(page.frames.size, 1)
  s.onPointerEnter({ pointerType: 'mouse' }); assert.equal(page.frames.size, 0)
  s.onPointerLeave(); assert.equal(page.frames.size, 1)
  s.onFocusIn({ target: { matches: () => true } }); assert.equal(page.frames.size, 0)
  s.onFocusOut({ currentTarget: { contains: () => false } }); assert.equal(page.frames.size, 1)
  s.onFocusIn({ target: { matches: () => false } }); assert.equal(page.frames.size, 1, 'mouse focus must not trap autoplay after resume')
  s.togglePlayback(); assert.equal(page.frames.size, 0)
  s.togglePlayback(); assert.equal(page.frames.size, 1)
  s.step(1); assert.equal(page.frames.size, 0); assert.equal(page.element.scrollLeft, 1376)
  page.settle(); assert.equal(page.frames.size, 1)
  page.doc.hidden = true; page.listeners.get('visibilitychange')(); assert.equal(page.frames.size, 0)
  page.doc.hidden = false; s.sync(); assert.equal(page.frames.size, 1)
  page.visible(false); assert.equal(page.frames.size, 0)
  page.visible(true); page.media.matches = true; page.listeners.get('media:change')(); assert.equal(page.frames.size, 0)
  s.step(-1); page.settle(); assert.equal(page.frames.size, 0, 'manual navigation remains available without autoplay')
  page.unmount(); assert.equal(page.timers.size, 0)
  for (const options of [{ reduced: true }, { intersection: false }]) {
    const fallback = mountPluginCarousel(options); fallback.visible(true)
    assert.equal(fallback.frames.size, 0); fallback.state.step(1); fallback.settle(); fallback.unmount()
  }
})

test('plugin rail drags horizontally without hijacking links or touch page scrolling', () => {
  const page = mountPluginCarousel(), s = page.state
  const pointer = { pointerType: 'mouse', button: 0, pointerId: 1, clientX: 500, target: { closest: () => null } }
  page.visible(true); s.onPointerDown(pointer); assert.equal(page.frames.size, 0)
  s.onPointerMove({ pointerId: 1, clientX: 400 }); assert.equal(page.element.scrollLeft, 1132)
  s.onPointerUp(pointer); assert.equal(page.frames.size, 1)
  s.onPointerDown({ ...pointer, pointerType: 'touch' }); assert.equal(page.frames.size, 0)
  s.onPointerUp(pointer); assert.equal(page.frames.size, 1)
  s.onPointerDown({ ...pointer, target: { closest: () => ({}) } }); assert.equal(page.frames.size, 1)
  page.unmount()
})

test('plugin carousel renders downloads and keeps duplicate copies out of keyboard navigation', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.ok(home.includes('aria-label="插件横向循环展示"'))
  assert.ok(home.includes('aria-label="暂停插件自动滚动"'))
  assert.ok(home.includes('aria-label="上一个插件"'))
  assert.ok(home.includes('aria-label="下一个插件"'))
  assert.equal((home.match(/class="carousel-group"/g) || []).length, 3)
  assert.equal((home.match(/class="carousel-group" aria-hidden="true"/g) || []).length, 2)
  assert.equal((home.match(/class="[^"]*\bhome-plugin-card\b[^"]*"/g) || []).length, 9)
  for (const copy of home.split('<div class="carousel-group"').slice(1)) {
    if (!copy.startsWith(' aria-hidden="true"')) continue
    const cards = copy.match(/<article class="[^"]*\bhome-plugin-card\b[^"]*"[\s\S]*?<\/article>/g)
    assert.equal(cards.length, 3)
    for (const card of cards) for (const control of card.match(/<(?:a|button)\b[^>]*>/g)) assert.ok(control.includes('tabindex="-1"'))
  }
  for (const p of plugins) assert.equal((home.match(new RegExp('href="' + p.download.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '"', 'g')) || []).length, 3)
})


test('dragging the full-card selection button does not activate a plugin', () => {
  const page = mountPluginCarousel(), s = page.state
  const event = { pointerType: 'mouse', button: 0, pointerId: 1, clientX: 500, target: { closest: selector => {
    assert.equal(selector, 'a,button:not(.plugin-demo-trigger)')
    return null
  } } }
  let prevented = 0, stopped = 0
  const click = { preventDefault() { prevented++ }, stopPropagation() { stopped++ } }
  s.onPointerDown(event); s.onPointerMove({ pointerId: 1, clientX: 400 }); s.onPointerUp(event)
  s.onClickCapture(click)
  assert.equal(prevented, 1); assert.equal(stopped, 1)
  s.onPointerDown(event); s.onPointerUp(event); s.onClickCapture(click)
  assert.equal(prevented, 1, 'a tap or click must reach the actual selection button')
  s.onClickCapture(click); assert.equal(prevented, 1, 'keyboard activation must remain available')
  s.onPointerDown(event); s.onPointerMove({ pointerId: 1, clientX: 400 }); s.onPointerUp({ ...event, type: 'pointercancel' })
  s.onClickCapture(click); assert.equal(prevented, 1, 'a canceled gesture must not block the next keyboard activation')
  page.unmount()
})

test('plugin cards expose full-card selection and render concrete feature details below', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  const panel = home.split('id="plugin-demo"')[1].split('plugin-compatibility')[0]
  for (const p of plugins) {
    assert.equal(p.featureDetails.length, p.features.length)
    assert.ok(home.includes('aria-label="查看' + p.name + '的具体功能"'))
    for (const detail of p.featureDetails) assert.ok(detail.length > 20)
  }
  assert.ok(panel.includes('比赛二路同步交互时间轴演示'))
  assert.ok(panel.includes('match-sync-demo'))
  assert.ok(panel.includes('aria-label="二路同步演示时间轴"'))
  assert.ok(panel.includes('aria-label="设为比赛主画面"'))
  assert.ok(panel.includes('跳转到合并弹幕'))
  assert.ok(!home.includes('已选中 · 功能在下方'))
  assert.ok(!home.includes('点击查看具体功能'))
  const selected = home.match(/<article class="[^"]*is-selected[^"]*"[\s\S]*?<\/article>/g)
  assert.equal(selected.length, 3)
  for (const card of selected) assert.ok(card.includes('比赛二路同步'))
  assert.ok(home.includes('id="home-intro"'))
  assert.ok(home.includes('aria-label="返回首页介绍"'))
  assert.ok(home.includes('aria-controls="plugin-demo"'))
})


test('plugin carousel initially shows the selected match-sync card', () => {
  const page = mountPluginCarousel({ initialIndex: 2 })
  assert.equal(page.element.scrollLeft, 1720)
  page.unmount()
})

function mountFeatureMotion({ reduced = false, intersection = true, plugin = plugins[0] } = {}) {
  const timers = new Map(), listeners = new Map()
  let mount, unmount, notify, next = 0, disconnected = false
  const media = { matches: reduced, addEventListener: (name, fn) => listeners.set('media:' + name, fn), removeEventListener: name => listeners.delete('media:' + name) }
  const doc = { hidden: false, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  class Observer { constructor(fn) { notify = fn } observe() {} disconnect() { disconnected = true } }
  const script = read('docs/.vitepress/theme/PluginFeatureMotion.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ phase, paused, playing, phaseLabels, phaseDescription, screenshot, sync, selectPhase, togglePlayback, onEnter, onLeave, onFocus, onBlur })', {
    defineProps: () => ({ plugin }), computed: fn => ({ get value() { return fn() } }), ref: value => ({ value: value === null ? {} : value }),
    onMounted: fn => { mount = fn }, onUnmounted: fn => { unmount = fn },
    window: { matchMedia: () => media, ...(intersection ? { IntersectionObserver: Observer } : {}) }, document: doc, IntersectionObserver: Observer,
    setTimeout: fn => { timers.set(++next, fn); return next }, clearTimeout: id => timers.delete(id)
  })
  mount()
  return { state, timers, listeners, media, doc, unmount, visible(value) { notify?.([{ isIntersecting:value }]) },
    tick() { const [id, fn] = timers.entries().next().value; timers.delete(id); fn() }, get disconnected() { return disconnected } }
}

test('platform feature illustration loops through quality, preview and chat only while watched', () => {
  const page = mountFeatureMotion(), s = page.state
  assert.equal(s.phaseDescription.value, plugins[0].featureDetails[0])
  assert.equal(page.timers.size, 0)
  page.visible(true)
  for (const expected of [1,2,0]) { page.tick(); assert.equal(s.phase.value, expected) }
  s.onEnter({ pointerType: 'mouse' }); assert.equal(page.timers.size, 0)
  s.onLeave(); assert.equal(page.timers.size, 1)
  s.onFocus({ target:{ matches:() => true } }); assert.equal(page.timers.size, 0)
  s.onBlur({ currentTarget:{ contains:() => false } }); assert.equal(page.timers.size, 1)
  s.togglePlayback(); assert.equal(page.timers.size, 0)
  s.selectPhase(2); assert.equal(s.phaseDescription.value, plugins[0].featureDetails[2])
  s.selectPhase(10); assert.equal(s.phase.value, 2)
  s.togglePlayback(); page.doc.hidden = true; page.listeners.get('visibilitychange')(); assert.equal(page.timers.size, 0)
  page.doc.hidden = false; s.sync(); assert.equal(page.timers.size, 1)
  page.visible(false); assert.equal(page.timers.size, 0)
  page.visible(true); page.media.matches = true; page.listeners.get('media:change')(); assert.equal(page.timers.size, 0)
  page.unmount(); assert.equal(page.timers.size, 0); assert.equal(page.listeners.size, 0); assert.ok(page.disconnected)
})

test('feature motion degrades to manual phases and platform demos show the matching screenshot', () => {
  for (const options of [{ reduced: true }, { intersection: false }]) {
    const page = mountFeatureMotion(options); page.visible(true)
    assert.equal(page.timers.size, 0); page.state.selectPhase(2); assert.equal(page.state.phase.value, 2); page.unmount()
  }
  for (const plugin of plugins.slice(0,2)) {
    const page = mountFeatureMotion({ plugin }), s = page.state
    s.selectPhase(2); assert.equal(s.screenshot.value, 'danmaku.webp'); assert.equal(s.phaseDescription.value, plugin.featureDetails[2])
    s.selectPhase(0); assert.equal(s.screenshot.value, 'landscape.webp'); page.unmount()
  }
})

function mountBackToIntro({ reduced = false, hasAnchor = true } = {}) {
  let mount, unmount, focused = false
  const listeners = new Map(), frames = new Map(), calls = []
  const win = { scrollY: 2500, innerHeight: 1000, matchMedia: () => ({ matches: reduced }), scrollTo: value => calls.push(value),
    addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  const script = read('docs/.vitepress/theme/BackToIntro.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ visible, introTop, update, returnToIntro })', {
    ref: value => ({ value }), onMounted: fn => { mount = fn }, onUnmounted: fn => { unmount = fn }, window: win,
    document: { documentElement: {}, getElementById: id => id === 'home-intro' ? hasAnchor && { getBoundingClientRect: () => ({ top: 1000 - win.scrollY }) } : { focus(options) { focused = options.preventScroll } } },
    getComputedStyle: () => ({ getPropertyValue: () => '64px' }), requestAnimationFrame: fn => { frames.set(1, fn); return 1 }, cancelAnimationFrame: id => frames.delete(id)
  })
  mount()
  return { state, win, calls, listeners, frames, unmount, get focused() { return focused } }
}

test('return button lands at the introduction with the wall behind it, never at scroll zero', () => {
  for (const reduced of [false,true]) {
    const page = mountBackToIntro({ reduced }), s = page.state
    assert.equal(s.visible.value, true); assert.equal(s.introTop(), 720)
    s.returnToIntro(); assert.equal(page.calls[0].top, 720)
    assert.equal(page.calls[0].behavior, reduced ? 'instant' : 'smooth'); assert.ok(page.focused)
    assert.ok(page.calls[0].top > 140, 'the navigation remains revealed beyond the splash threshold')
    page.win.scrollY = 720; s.update(); assert.equal(s.visible.value, false)
    page.listeners.get('scroll')(); page.unmount(); assert.equal(page.frames.size, 0); assert.equal(page.listeners.size, 0)
  }
  const missing = mountBackToIntro({ hasAnchor: false }); missing.state.returnToIntro(); assert.equal(missing.calls.length, 0); assert.equal(missing.state.visible.value, false); missing.unmount()
})


function mountMatchTimeline({ reduced = false, intersection = true } = {}) {
  const frames = new Map(), listeners = new Map()
  let mount, unmount, notify, next = 0, disconnected = false
  const media = { matches:reduced, addEventListener:(name,fn) => listeners.set('media:' + name,fn), removeEventListener:name => listeners.delete('media:' + name) }
  const doc = { hidden:false, addEventListener:(name,fn) => listeners.set(name,fn), removeEventListener:name => listeners.delete(name) }
  class Observer { constructor(fn) { notify = fn } observe() {} disconnect() { disconnected = true } }
  const script = read('docs/.vitepress/theme/MatchSyncTimeline.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ chapters, duration, time, active, alignment, delay, paused, reduced, playing, mainVolume, secondaryVolume, merged, clock, sync, seek, jump, togglePlayback, hold, onFocus, onBlur })', {
    computed:fn => ({ get value() { return fn() } }), ref:value => ({ value:value === null ? {} : value }),
    onMounted:fn => { mount = fn }, onUnmounted:fn => { unmount = fn }, window:{ matchMedia:() => media, ...(intersection ? { IntersectionObserver:Observer } : {}) }, document:doc, IntersectionObserver:Observer,
    requestAnimationFrame:fn => { frames.set(++next,fn); return next }, cancelAnimationFrame:id => frames.delete(id)
  })
  mount()
  return { state, frames, listeners, media, doc, unmount, visible(value) { notify?.([{ isIntersecting:value }]) },
    frame(now) { const [id,fn] = frames.entries().next().value; frames.delete(id); fn(now) }, get disconnected() { return disconnected } }
}

test('match-sync timeline chapters and scrubbing control one continuous playhead', () => {
  const page = mountMatchTimeline(), s = page.state
  assert.equal(s.duration,22)
  assert.equal(s.chapters.length,5)
  for (const [time,index] of [[0,0],[3.9,0],[4,1],[8,2],[12.9,2],[13,3],[17,4],[22,4]]) {
    s.seek(time); assert.equal(s.active.value,index)
  }
  s.seek(10.5); assert.equal(s.alignment.value,.5); assert.equal(s.delay.value,1.6)
  s.seek(13); assert.equal(s.alignment.value,1); assert.equal(s.delay.value,0)
  s.seek(-100); assert.equal(s.time.value,0)
  s.seek(100); assert.equal(s.time.value,22)
  s.jump(3); assert.equal(s.time.value,13)
  s.jump(99); assert.equal(s.time.value,13)
  assert.equal(s.clock(9),'0:09'); assert.equal(s.clock(728),'12:08')
  assert.equal(s.mainVolume.value,70); assert.equal(s.secondaryVolume.value,40); assert.equal(s.merged.value,true)
  page.unmount()
})

test('match-sync playback advances by time, loops and pauses while seeking or in background', () => {
  const page = mountMatchTimeline(), s = page.state
  assert.equal(page.frames.size,0); page.visible(true); assert.equal(page.frames.size,1)
  page.frame(100); page.frame(200); assert.ok(Math.abs(s.time.value - .1) < .001)
  s.hold(true); assert.equal(page.frames.size,0); s.seek(10.5); assert.equal(s.time.value,10.5)
  s.hold(false); assert.equal(page.frames.size,1)
  s.onFocus({ target:{ matches:() => true } }); assert.equal(page.frames.size,0)
  s.onBlur({ currentTarget:{ contains:() => false } }); assert.equal(page.frames.size,1)
  s.onFocus({ target:{ matches:() => false } }); assert.equal(page.frames.size,1, 'play button keyboard focus must not prevent playback')
  s.togglePlayback(); assert.equal(page.frames.size,0)
  s.seek(22); s.togglePlayback(); assert.equal(s.time.value,0)
  s.seek(21.95); page.frame(1000); page.frame(1100); assert.ok(s.time.value < .1)
  page.doc.hidden = true; page.listeners.get('visibilitychange')(); assert.equal(page.frames.size,0)
  page.doc.hidden = false; s.sync(); assert.equal(page.frames.size,1)
  page.visible(false); assert.equal(page.frames.size,0)
  page.visible(true); page.media.matches = true; page.listeners.get('media:change')(); assert.equal(page.frames.size,0)
  page.unmount(); assert.equal(page.frames.size,0); assert.equal(page.listeners.size,0); assert.ok(page.disconnected)
})

test('match-sync reduced-motion and observer fallback preserve manual chapter navigation', () => {
  for (const options of [{ reduced:true }, { intersection:false }]) {
    const page = mountMatchTimeline(options), s = page.state
    page.visible(true); assert.equal(page.frames.size,0)
    s.jump(4); assert.equal(s.time.value,17); assert.equal(s.active.value,4)
    s.seek(9); assert.equal(s.active.value,2); s.togglePlayback(); assert.equal(page.frames.size,0)
    page.unmount()
  }
})

test('built match-sync showcase exposes five chapters, a native scrubber and interactive canvas', () => {
  const home = readFileSync(resolve(dist,'index.html'),'utf8')
  for (const label of ['选择主画面','框选计时区域','自动对齐','混合解说','合并弹幕']) assert.ok(home.includes('aria-label="跳转到' + label + '"'))
  assert.match(home, /class="timeline-seek"[^>]*type="range"[^>]*max="22"/)
  assert.ok(home.includes('class="playhead-clip"'))
  assert.ok(home.includes('aria-label="设为比赛主画面"'))
  assert.ok(home.includes('aria-label="暂停二路同步演示"'))
  assert.ok(home.includes('交互演示'))
})
