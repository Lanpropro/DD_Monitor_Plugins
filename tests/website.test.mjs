import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { resolve, relative, dirname } from 'node:path'
import { runInNewContext } from 'node:vm'
import { createAppearanceToggle } from '../docs/.vitepress/theme/appearance.mjs'
import { TOUR_CHAPTERS, TOUR_DURATION, viewingTourFrame } from '../docs/.vitepress/theme/viewing-tour.mjs'

const root = resolve(import.meta.dirname, '..')
const read = path => readFileSync(resolve(root, path), 'utf8')
const plugins = JSON.parse(read('docs/data/plugins.json'))
const dist = resolve(root, 'docs/.vitepress/dist')
const base = '/DD_Monitor_Plugins/'

test('homepage uses the approved product name and viewing copy', () => {
  const source = read('docs/.vitepress/theme/HomePage.vue')
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  for (const text of ['DD_Monitor_CE', '横屏、竖屏，按你的方式观看。', '准备好你的下一场观看']) {
    assert.ok(source.includes(text))
    assert.ok(home.includes(text))
  }
  assert.ok(!source.includes('下载 Windows 便携版，完整解压后即可开始。'))
})

// Execute the component's actual setup with browser/lifecycle boundaries mocked.
function mountShowcase({ reduced = false, intersection = true, delay = 0 } = {}) {
  let preferenceChanged
  const media = { matches: reduced, addEventListener(event, callback) { preferenceChanged = callback }, removeEventListener() {} }
  const animations = []
  const classes = new Set()
  const active = []
  const target = { dataset: { revealDelay: String(delay) }, animate: (...args) => {
    animations.push(args)
    const animation = { cancelled: false, cancel() { this.cancelled = true } }
    active.push(animation)
    return animation
  }, classList: {
    add: name => classes.add(name), remove: name => classes.delete(name)
  } }
  const observed = new Set()
  let notify, mount, unmount, observerOptions, disconnected = false
  class Observer {
    constructor(callback, options) { notify = callback; observerOptions = options }
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
  return { media, animations, active, observed, classes, unmount, observerOptions,
    preferenceChanged: () => preferenceChanged?.(),
    outside: () => notify?.([{ target, isIntersecting: false }]),
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
test('scroll entrances wait for visibility, stagger without flashing and cancel active motion', () => {
  const page = mountShowcase({ delay: 120 })
  page.outside()
  assert.equal(page.animations.length, 0)
  assert.equal(page.observerOptions.threshold, 0, 'tall previews reveal from their leading edge')
  assert.equal(page.observerOptions.rootMargin, '0px 0px -80px 0px')
  page.visible()
  assert.equal(page.animations[0][1].delay, 120)
  assert.equal(page.animations[0][1].fill, 'backwards', 'staggered content stays hidden during the delay')
  page.media.matches = true
  page.preferenceChanged()
  assert.ok(page.active[0].cancelled)
  page.unmount()
  const pending = mountShowcase()
  pending.media.matches = true
  pending.preferenceChanged()
  assert.equal(pending.classes.size, 0, 'unseen content becomes readable when motion is reduced')
  assert.ok(pending.disconnected)
  pending.unmount()
  const leaving = mountShowcase()
  leaving.visible()
  leaving.unmount()
  assert.ok(leaving.active[0].cancelled)
})

test('prototype palette and scroll entrances cover the page beyond the facts row', () => {
  const css = read('docs/.vitepress/theme/style.css')
  for (const token of ['--vp-c-bg: #f5f2ea', '--vp-c-bg: #070c16', '--vp-c-brand-1: #087a9e', '--vp-c-brand-1: #5cd6ff', '--button-primary-bg: #f5f2ea', '--button-radius: 999px']) assert.ok(css.includes(token), token)
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  for (const className of ['hero-copy', 'hero-intro', 'demo-selector', 'demo-preview', 'plugin-demo-heading', 'sync-screen', 'sync-timeline', 'bottom-actions']) {
    assert.ok(home.match(/<[^>]+>/g).some(tag => tag.includes('data-reveal') && new RegExp('class="' + className + '(?: [^"]*)?"').test(tag)), className + ' has its own scroll entrance')
  }
  const timeline = read('docs/.vitepress/theme/MatchSyncTimeline.vue')
  assert.ok(timeline.includes('--sync-accent:var(--vp-c-brand-1)'))
  assert.ok(!timeline.includes('#9881ff'))
})

test('dark surfaces separate content from the page and software imagery stays at full opacity', () => {
  const css = read('docs/.vitepress/theme/style.css')
  const dark = css.split('.dark {')[1].split('}')[0]
  const token = name => dark.match(new RegExp('--' + name + ': ([^;]+);'))[1]
  const luminance = hex => {
    const channels = hex.slice(1).match(/../g).map(part => parseInt(part, 16) / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722
  }
  const page = luminance(token('vp-c-bg'))
  const panel = luminance(token('vp-c-bg-soft'))
  assert.ok(page < .01, 'the page keeps a near-black background')
  assert.ok((panel + .05) / (page + .05) > 1.15, 'panels remain distinct from the page')
  assert.equal(token('vp-c-divider'), 'rgba(255, 255, 255, .14)')
  const imageRule = read('docs/.vitepress/theme/MatchSyncTimeline.vue').match(/\.sync-software \{([^}]+)\}/)[1]
  assert.ok(!/opacity\s*:|filter\s*:/.test(imageRule), 'the real software screenshot is not dimmed or desaturated')
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

test('the replacement screenshots are compact WebP images rather than renamed PNGs', () => {
  for (const name of ['landscape', 'danmaku', 'portrait']) {
    const screenshot = readFileSync(resolve(root, `docs/public/screenshots/${name}.webp`))
    assert.equal(screenshot.toString('ascii', 0, 4), 'RIFF')
    assert.equal(screenshot.toString('ascii', 8, 12), 'WEBP')
    assert.ok(screenshot.length < 1024 * 1024, `${name} must stay below 1 MiB`)
  }
})

test('website screenshot derivatives remove only the native title bar', () => {
  for (const [name,width,height] of [['landscape',1920,1064],['danmaku',1920,1064],['portrait',1080,1702]]) {
    const bytes = readFileSync(resolve(root, `docs/public/screenshots/${name}.webp`))
    const frame = bytes.indexOf(Buffer.from([0x9d,0x01,0x2a]))
    assert.ok(frame > 0, 'a WebP VP8 frame header is present')
    assert.equal(bytes.readUInt16LE(frame + 3) & 0x3fff,width)
    assert.equal(bytes.readUInt16LE(frame + 5) & 0x3fff,height)
  }
  assert.ok(!read('docs/.vitepress/theme/FeatureDemo.vue').includes('mini-titlebar'))
})

test('homepage exposes software and every plugin download before JavaScript runs', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.ok(home.includes('href="/DD_Monitor_Plugins/download"'))
  assert.ok(home.includes('id="plugins"'))
  assert.ok(home.includes('screenshots/landscape.webp'))
  assert.ok(!home.includes('查看完整截图'), 'the presentation should not show a redundant screenshot link')
  assert.ok(!home.includes('gallery-playback'), 'the screenshot must not have an overlaid playback button')
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

test('opening screen shows the recorded tiles in the placeholder wall before any page content', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  const wall = home.match(/<section class="splash"[^>]*>/)
  const wallContent = home.match(/<section class="splash"[\s\S]*?<\/section>/)?.[0]
  assert.ok(wall, 'missing opening wall')
  assert.match(wall[0], /aria-hidden="true"/, 'the decorative wall must stay out of the accessibility tree')
  const wallTiles = (home.match(/class="splash-tile"/g) || []).length
  assert.equal(wallTiles, 224, 'the wall repeats its 56 tiles twice per axis so it wraps without a seam')
  const splash = read('docs/.vitepress/theme/SplashWall.vue')
  assert.ok(!home.includes('splash-ring'), 'recordings must live in the wall rather than a separate overlay')
  for (let index = 1; index <= 55; index++) {
    const file = String(index).padStart(2, '0') + '.webp'
    assert.ok(existsSync(resolve(root, `docs/public/wall/${file}`)))
    assert.equal((wallContent.match(new RegExp(`${base}wall/${file}`, 'g')) || []).length, 4, `${file} must appear in all four repeated wall quadrants`)
  }
  assert.equal((wallContent.match(/\/wall\/\d{2}\.webp/g) || []).length, 220, 'only the supplied recordings replace placeholders')
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

test('idle drift keeps the original compact orbit speed and amplitude', () => {
  const splash = read('docs/.vitepress/theme/SplashWall.vue')
  assert.match(splash, /driftX = Math\.sin\(now \/ 2600\) \* 62 \+ Math\.sin\(now \/ 7100\) \* 22/, 'horizontal drift must keep the original speed')
  assert.match(splash, /driftY = Math\.cos\(now \/ 3300\) \* 44 \+ Math\.cos\(now \/ 8300\) \* 16/, 'vertical drift must keep the original speed')
})

test('supplied recordings spiral outward from the center without overlapping tiles', () => {
  const splash = read('docs/.vitepress/theme/SplashWall.vue')
  const ring = splash.match(/const RING_OFFSETS = (\[[\s\S]*?\])\nconst /)?.[1]
  assert.ok(ring, 'missing the center-out tile order')
  assert.match(splash, /function buildImageTiles\(gridColumns\)/, 'recordings must be mapped onto the repeated wall')
  assert.match(splash, /for \(let quadrantRow = 0; quadrantRow < 2;/, 'the wall needs all four repeated quadrants')
  assert.match(splash, /centerShift = \{[\s\S]*?columns\.value \* 2[\s\S]*?rows \* 2/, 'the center tile needs a half-cell alignment')
  const offsets = [...ring.matchAll(/\[(-?\d+),\s*(-?\d+)\]/g)].map(match => `${match[1]},${match[2]}`)
  assert.deepEqual(offsets.slice(0, 9), ['0,0', '1,0', '1,1', '0,1', '-1,1', '-1,0', '-1,-1', '0,-1', '1,-1'],
    'tiles must follow numpad 5, 6, 3, 2, 1, 4, 7, 8, 9')
  assert.deepEqual(offsets.slice(9, 20), ['2,-1', '2,0', '2,1', '2,2', '1,2', '0,2', '-1,2', '-2,2', '-2,1', '-2,0', '-2,-1'],
    'recordings 18–20 must continue the existing spiral without moving recordings 01–17')
  assert.deepEqual(offsets.slice(20, 29), ['-2,-2', '-1,-2', '0,-2', '1,-2', '2,-2', '3,-2', '3,-1', '3,0', '3,1'],
    'recordings 21–29 must continue around the upper and right edges of the existing spiral')
  assert.deepEqual(offsets.slice(29, 52), ['3,2', '3,3', '2,3', '1,3', '0,3', '-1,3', '-2,3', '-3,3', '-3,2', '-3,1', '-3,0', '-3,-1', '-3,-2', '-3,-3', '-2,-3', '-1,-3', '0,-3', '1,-3', '2,-3', '3,-3', '3,4', '2,4', '1,4'],
    'recordings 30–52 must fill the outer edge and available bottom row without moving earlier recordings')
  assert.deepEqual(offsets.slice(52), ['0,4', '-1,4', '-2,4'], 'recordings 53–55 must continue along the remaining bottom row')
  assert.equal(offsets.length, 55, 'every supplied recording needs one position')
  assert.equal(new Set(offsets).size, offsets.length, 'tile positions must not overlap')
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.ok(!home.includes('class="splash-ring"'), 'the recordings must not be rendered as a separate centered layer')
  assert.match(home, /class="splash-tile"[^>]*>\s*<img[^>]+wall\/01\.webp/, 'the first recording must render inside a wall tile')
})

test('all fifty-five recordings survive desktop and mobile wall repetition without overwriting each other', () => {
  const script = read('docs/.vitepress/theme/SplashWall.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const { buildImageTiles, wrap } = runInNewContext(script + '\n;({ buildImageTiles, wrap })', {
    ref: value => ({ value }), reactive: value => value, withBase: value => value,
    onMounted() {}, onUnmounted() {}
  })
  assert.equal(wrap(501, 1000), -499, 'dragging across a period must wrap to the matching repeated wall')
  assert.equal(wrap(-501, 1000), 499)
  assert.equal(wrap(2501, 1000), -499, 'long drags must retain their phase across multiple periods')
  for (const columns of [14, 8]) {
    const tiles = buildImageTiles(columns)
    assert.equal(tiles.size, 220, `${columns} columns must retain all four copies of all fifty-five recordings`)
    for (let recording = 1; recording <= 55; recording++) {
      assert.equal([...tiles.values()].filter(value => value === recording).length, 4)
    }
    for (const tile of tiles.keys()) assert.ok(tile >= 1 && tile <= 224)
    const rowPeriod = 56 / (columns / 2)
    for (const [tile, recording] of tiles) {
      const row = Math.floor((tile - 1) / columns), column = (tile - 1) % columns
      assert.equal(tiles.get(row * columns + (column + columns / 2) % columns + 1), recording)
      assert.equal(tiles.get((row + rowPeriod) % (224 / columns) * columns + column + 1), recording)
    }
  }
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
  assert.ok(home.includes('role="separator"'))
  assert.ok(home.includes('aria-label="主画面宽度"'))
  assert.ok(!home.includes('比例支持拖动'))
  assert.ok(home.includes('aria-label="布局预设页面"'))
  const miniature = home.match(/<div class="software-miniature[\s\S]*?<p class="demo-status"/)?.[0]
  assert.ok(miniature?.includes('id="mini-layout-presets"'), 'the preset page belongs inside the software preview')
  assert.ok(!home.includes('<details id="mini-layout-presets"'))
  for (const label of ['摆放直播', '调整比例', '控制声音', '观看体验演示进度']) assert.ok(home.includes(label))
  assert.ok(home.includes('aria-controls="mini-layout-presets"'))
  assert.ok(home.includes(`aria-label="选择${mountMiniature().state.cards[0].name}"`))
  assert.ok(!home.includes('观看组合'))
  assert.ok(home.includes('id="plugin-demo"'))
  for (const plugin of plugins) {
    assert.equal(plugin.steps.length, 3)
    assert.equal(new Set(plugin.steps).size, 3)
  }
  for (const chapter of ['对照画面','偏移微调','同步观看','合并弹幕']) assert.ok(home.includes(chapter))
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
  const state = runInNewContext(script + '\n;({ active, progress, controlsReady, productHeight, enhanced, selectShot, interact, update, preferenceChanged })', {
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
  gallery.state.selectShot(2)
  assert.equal(gallery.state.active.value, 2)
  assert.equal(gallery.timers.size, 1, 'manual selection restarts the automatic slideshow')
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

test('restored screenshot contraction starts at the sticky offset and spans the scroll tail', () => {
  const gallery = mountGallery()
  // The 600px scroll range is the full distance between the sticky start and the tail end.
  gallery.story.top = 160
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 0)
  gallery.story.top = 100
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 0)
  gallery.story.top = -440
  gallery.state.update()
  assert.ok(Math.abs(gallery.state.progress.value - .9) < .001)
  gallery.unmount()
})

test('the sticky screenshot and facts stay vertically centered with compact controls', () => {
  const source = read('docs/.vitepress/theme/ScreenshotShowcase.vue')
  const home = read('docs/.vitepress/theme/HomePage.vue')
  const styles = read('docs/.vitepress/theme/style.css')
  const pin = source.match(/\.screenshot-pin \{([^}]+)\}/)[1]
  const enhancedPin = source.match(/\.is-enhanced \.screenshot-pin \{([^}]+)\}/)[1]
  const tail = source.match(/\.is-enhanced::after \{([^}]+)\}/)[1]
  const controls = source.match(/\.gallery-controls \{([^}]+)\}/)[1]
  const details = source.match(/\.showcase-details \{([^}]+)\}/)[1]
  assert.match(pin, /justify-content:\s*center/, 'the gallery is centered inside its sticky viewport')
  assert.match(pin, /gap:\s*12px/, 'the controls stay close to the screenshot')
  assert.match(controls, /gap:\s*8px/, 'the controls and source note form one compact group')
  assert.match(details, /gap:\s*14px/, 'the facts row joins the centered showcase group')
  assert.match(styles, /\.facts \{[^}]*width:\s*100%/, 'the facts row keeps the full container width')
  assert.match(styles, /\.facts \{[^}]*padding:\s*26px 0 38px/, 'the facts row keeps its vertical padding')
  assert.match(enhancedPin, /min-height:\s*calc\(100svh - var\(--vp-nav-height\) - 48px\)/,
    'the sticky viewport spans the area below the navigation bar')
  assert.match(tail, /height:\s*45svh/, 'the original scroll distance is restored')
  assert.match(source, /--shot-scale': enhanced \? 1 - progress \* \.18 : 1/, 'the original contraction is restored')
  assert.ok(source.includes('<slot />'), 'the facts row is rendered inside the sticky showcase')
  const facts = home.indexOf('<div class="facts"')
  assert.ok(facts > home.indexOf('<ScreenshotShowcase>') && facts < home.indexOf('</ScreenshotShowcase>'),
    'the facts row is no longer outside the centered group')
  const gallery = mountGallery()
  gallery.story.top = -500
  gallery.state.update()
  assert.equal(gallery.state.progress.value, 1, 'the fully revealed state still reaches the center')
  gallery.unmount()
})

test('the facts row enters with the later part of the screenshot scroll', () => {
  const source = read('docs/.vitepress/theme/ScreenshotShowcase.vue')
  const home = read('docs/.vitepress/theme/HomePage.vue')
  assert.match(source, /'--facts': controlsReady \|\| !enhanced \? 1 : Math\.min\(1, Math\.max\(0, \(progress - \.62\) \/ \.32\)\)/,
    'the facts row starts after the screenshot controls')
  const rule = source.match(/:slotted\(\.facts\) \{([^}]+)\}/)[1]
  assert.match(rule, /opacity:\s*var\(--facts\)/, 'the row fades in with scroll progress')
  assert.match(rule, /translateY\(calc\(\(1 - var\(--facts\)\) \* 18px\)\)/, 'the row rises into place')
  assert.ok(!home.includes('<div class="facts"><div data-reveal'),
    'the generic entrance does not compete with the scroll-linked row')
})

test('revealed screenshot controls stay visible during a ratio change', () => {
  assert.match(read('docs/.vitepress/theme/ScreenshotShowcase.vue'), /'--frame-ratio': active === 2 \? 5 \/ 8 : 16 \/ 9/, 'the new portrait screenshot keeps its actual 5:8 proportions')
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


function mountMiniature({ empty = true, reduced = false, observer = true } = {}) {
  let target = null, nullRefs = 0, backFocus = 0, mount, unmount, notify, preferenceChanged, next = 0, disconnected = false
  const frames = new Map(), listeners = new Map()
  const media = { matches: reduced, addEventListener(name, fn) { preferenceChanged = fn }, removeEventListener() {} }
  const doc = { hidden: false, elementFromPoint: () => ({ closest: () => target }), addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) }
  const rect = { left: 200, top: 100, width: 600, height: 400 }
  const surface = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 520 }), querySelector: () => ({ getBoundingClientRect: () => rect }) }
  const followList = { clientHeight: 250, scrollTop: 0, children: Array.from({ length: 6 }, (_, i) => ({ offsetTop: i * 82, offsetHeight: 77 })) }
  const rootElement = { contains: element => element.inside, querySelector: selector => selector === '.mini-follow-list' ? followList : selector === '.software-miniature' ? surface : selector === '.mini-wall' ? { getBoundingClientRect: () => rect } : { focus() { backFocus++ } } }
  class Observer { constructor(fn) { notify = fn } observe() {} disconnect() { disconnected = true } }
  const script = read('docs/.vitepress/theme/FeatureDemo.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ presets, preset, presetId, columns, cellStyle, slots, cards, selected, dragged, over, light, pickerOpen, selectCard, placeCard, clearSlot, changePreset, onCardDown, onCardMove, onCardUp, cancelCard, openPicker, closePicker, scenes, scene, manual, held, reduced, tourTime, tourFrame, sceneDuration, playbackTime, clock, seekTour, selectScene, replayTour, scrubTour, takeControl, ratio, volumes, muted, setRatio, setVolume, toggleMute, resizeDown, resizeMove, resizeUp })', {
    computed: fn => ({ get value() { return fn() } }),
    nextTick: fn => fn(),
    ref: value => ({ value: value === null && nullRefs++ === 1 ? rootElement : value }),
    TOUR_CHAPTERS, TOUR_DURATION, viewingTourFrame, withBase: value => value,
    onMounted: fn => { mount = fn }, onUnmounted: fn => { unmount = fn },
    requestAnimationFrame: fn => { frames.set(++next, fn); return next }, cancelAnimationFrame: id => frames.delete(id),
    document: doc, window: { matchMedia: () => media, ...(observer ? { IntersectionObserver: Observer } : {}) }, IntersectionObserver: Observer
  })
  if (empty) state.slots.value = Array(16).fill(null)
  return { state, frames, doc, media, listeners, mount, unmount, followList,
    visible(value) { notify?.([{ isIntersecting: value }]) }, preferenceChanged() { preferenceChanged() },
    tick(now) { const [id, fn] = frames.entries().next().value; frames.delete(id); fn(now) },
    get disconnected() { return disconnected }, get backFocus() { return backFocus }, hit(index, inside = true) { target = index === null ? null : { dataset: { miniSlot: String(index) }, inside } } }
}

test('the virtual pointer and the demonstrated layout, ratio and sound share the same seekable clock', () => {
  const page = mountMiniature({ empty: false }), s = page.state
  assert.equal(s.slots.value.filter(Boolean).length, 6, 'real thumbnails are visible before hydration')
  assert.equal(new Set(s.slots.value.filter(Boolean).map(card => card.image)).size, 6)
  s.seekTour(1800); assert.equal(s.pickerOpen.value, true)
  for (let i = 0; i < 6; i++) {
    const pickup = 4200 + i * 2400, id = String.fromCharCode(97 + i)
    s.seekTour(pickup + 700)
    assert.equal(s.presetId.value, 'corner'); assert.equal(s.preset.value.cells.length, 6)
    assert.equal(s.tourFrame.value.ghost, id); assert.equal(s.slots.value.filter(Boolean).length, i)
    assert.equal(s.tourFrame.value.to, `slot-${i}`)
    assert.equal(s.tourFrame.value.followIndex, i); assert.equal(s.tourFrame.value.followProgress, 1)
    s.seekTour(pickup + 1400)
    assert.equal(s.slots.value[i].id, id); assert.equal(s.tourFrame.value.ghost, null)
  }
  assert.deepEqual(Array.from(s.slots.value.slice(0, 6), card => card.id), ['a', 'b', 'c', 'd', 'e', 'f'])
  assert.equal(page.followList.scrollTop, 237, 'the sixth full-height card is scrolled into view')
  s.seekTour(15700); assert.equal(page.followList.scrollTop, 196, 'scrolling follows the seekable clock')
  s.seekTour(1800); assert.equal(s.pickerOpen.value, true, 'seeking backward restores the picker and earlier state')
  assert.equal(page.followList.scrollTop, 0, 'rewinding restores the top of the follow list')
  s.selectScene('ratio'); s.seekTour(8100); assert.equal(s.ratio.value, 78)
  s.seekTour(15000); assert.equal(s.ratio.value, 55)
  s.selectScene('sound'); s.seekTour(13500); assert.equal(s.volumes.value[0], 15); assert.equal(s.volumes.value[1], 40)
  assert.equal(s.muted.value[1], true); assert.equal(s.muted.value[0], false)
  s.seekTour(0); assert.equal(s.muted.value[1], false); assert.equal(s.volumes.value[0], 40)
})

test('the viewing chapter timeline seeks continuously through all three slower demonstrations', () => {
  const s = mountMiniature().state
  assert.equal(TOUR_DURATION, 58000)
  assert.equal(s.clock(TOUR_DURATION), '0:58')
  assert.equal(viewingTourFrame('layout', 15700).followProgress, .5)
  assert.equal(viewingTourFrame('layout', 0).followIndex, 0)
  const source = read('docs/.vitepress/theme/FeatureDemo.vue')
  assert.ok(source.includes('overflow-y: auto'))
  assert.ok(source.includes('flex: 0 0 clamp(68px, 7vw, 96px)'))
  for (const [time, scene, local] of [[0, 'layout', 0], [21900, 'layout', 21900], [22000, 'ratio', 0], [32500, 'ratio', 10500], [40000, 'sound', 0], [58000, 'sound', 18000]]) {
    s.scrubTour({ target: { value: String(time) } })
    assert.equal(s.scene.value, scene); assert.equal(s.tourTime.value, local)
    assert.equal(s.playbackTime.value, time); assert.equal(s.held.value, true)
  }
  s.selectScene('ratio'); assert.equal(s.playbackTime.value, 22000)
  s.selectScene('layout'); assert.equal(s.playbackTime.value, 0)
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.ok(home.includes('aria-label="观看体验分段时间轴"'))
  assert.ok(home.includes('class="tour-playhead-clip"'))
  assert.equal((home.match(/class="tour-chapter"/g) || []).length, 3)
})

test('the viewing tour stops offscreen, in the background, on takeover and with reduced motion', () => {
  const page = mountMiniature(), s = page.state
  page.mount(); assert.equal(page.frames.size, 0)
  page.visible(true); page.tick(100); page.tick(116); assert.equal(s.tourTime.value, 16)
  s.scrubTour({ target: { value: '4900' } }); assert.equal(s.held.value, true); assert.equal(s.manual.value, false)
  assert.equal(s.tourFrame.value.ghost, 'a'); assert.equal(page.frames.size, 0, 'scrubbing freezes the virtual pointer along with the scene')
  page.visible(false); page.visible(true); assert.equal(page.frames.size, 0)
  s.replayTour(); assert.equal(s.held.value, false); assert.equal(page.frames.size, 1)
  s.seekTour(s.sceneDuration.value - 1); page.tick(200); page.tick(216); assert.equal(s.scene.value, 'ratio'); assert.equal(s.tourTime.value, 0)
  page.visible(false); assert.equal(page.frames.size, 0)
  page.visible(true); s.takeControl(); assert.equal(page.frames.size, 0)
  page.visible(false); page.visible(true); assert.equal(page.frames.size, 0, 'visibility must not overwrite manual work')
  s.selectScene('sound'); assert.equal(page.frames.size, 1)
  page.doc.hidden = true; page.listeners.get('visibilitychange')(); assert.equal(page.frames.size, 0)
  page.doc.hidden = false; page.listeners.get('visibilitychange')(); assert.equal(page.frames.size, 1)
  page.media.matches = true; page.preferenceChanged(); assert.equal(s.reduced.value, true); assert.equal(s.manual.value, true)
  assert.equal(page.frames.size, 0); assert.equal(s.tourFrame.value.muted, true)
  page.unmount(); assert.equal(page.disconnected, true); assert.equal(page.listeners.size, 0)
  const fallback = mountMiniature({ observer: false }); fallback.mount(); assert.equal(fallback.state.manual.value, true); assert.equal(fallback.frames.size, 0)
  const quiet = mountMiniature({ reduced: true }); quiet.mount(); quiet.visible(true)
  assert.equal(quiet.frames.size, 0); assert.equal(quiet.state.manual.value, true); assert.equal(quiet.state.slots.value.filter(Boolean).length, 6)
})

test('resizing the main preview preserves independent bottom regions and uncropped stream images', () => {
  const s = mountMiniature({ empty: false }).state
  const cells = s.preset.value.cells
  const before = cells.map((cell, i) => s.cellStyle(cell, i))
  s.setRatio(78)
  const after = cells.map((cell, i) => s.cellStyle(cell, i))
  assert.notEqual(before[0].width, after[0].width)
  assert.notEqual(before[1].width, after[1].width)
  for (let i = 3; i < 6; i++) assert.deepEqual(before[i], after[i], 'bottom widths are independent from the main divider')
  s.changePreset('2x2'); assert.equal(s.cellStyle(s.preset.value.cells[0], 0).gridArea, '1 / 1 / span 1 / span 1')
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  assert.equal((home.match(/class="mini-stream-image"/g) || []).length, 6)
  for (const card of s.cards) assert.ok(home.includes(`/wall/${card.image}`))
  const source = read('docs/.vitepress/theme/FeatureDemo.vue')
  assert.match(source, /\.mini-stream-image \{[^}]*object-fit: contain/)
  assert.ok(!home.includes('class="mini-titlebar"')); assert.ok(home.includes('class="mini-live"'))
})

test('manual ratio and sound controls affect only their target and clamp to supported bounds', () => {
  const { state: s } = mountMiniature()
  const event = x => ({ clientX: x, pointerId: 1, currentTarget: { setPointerCapture() {}, releasePointerCapture() {} } })
  s.resizeDown(event(650)); assert.equal(s.ratio.value, 75); assert.equal(s.manual.value, true)
  s.resizeMove(event(2000)); assert.equal(s.ratio.value, 82)
  s.resizeUp(event(2000)); s.resizeMove(event(200)); assert.equal(s.ratio.value, 82)
  s.setRatio(0); assert.equal(s.ratio.value, 40)
  s.setVolume(0, 200); assert.equal(s.volumes.value[0], 100); assert.equal(s.volumes.value[1], 40)
  s.toggleMute(1); assert.equal(s.muted.value[1], true); assert.equal(s.muted.value[0], false)
})

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

test('viewing and plugin demos restore the pre-fusion presentation without layout shortcuts', () => {
  const home = readFileSync(resolve(dist, 'index.html'), 'utf8')
  const viewing = home.split('class="home-section viewing-section"')[1].split('id="plugins"')[0]
  assert.ok(viewing.indexOf('class="demo-selector"') < viewing.indexOf('class="demo-preview"'))
  assert.ok(!viewing.includes('layout-portrait'))
  assert.ok(!viewing.includes('layout-quick-tabs'))
  assert.ok(!viewing.includes('layout-options'))
  assert.ok(viewing.includes('aria-controls="mini-layout-presets"'))
  assert.ok(viewing.includes(`aria-label="选择${mountMiniature().state.cards[0].name}"`))
  assert.ok(home.includes('aria-label="二路同步演示时间轴"'))
  const css = read('docs/.vitepress/theme/style.css')
  assert.ok(!css.includes('max-width: 880px'))
  assert.ok(css.includes('.plugin-demo-panel { display:block;'))
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
    assert.ok(p.features.length > 0)
    assert.ok(home.includes('aria-label="查看' + p.name + '的具体功能"'))
  }
  assert.ok(panel.includes('比赛二路同步实录预览'))
  assert.ok(panel.includes('match-sync-demo'))
  assert.ok(panel.includes('aria-label="二路同步演示时间轴"'))
  assert.ok(panel.includes('videos/match-sync-recording.mp4'))
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

function mountPluginRecording({ plugin = plugins[0], intersection = true } = {}) {
  let mount, unmount, notify, disconnected = false, refs = 0
  const listeners = new Map()
  const video = { paused:false, pause() { this.paused = true } }
  const doc = { hidden:false, addEventListener:(name,fn) => listeners.set(name,fn), removeEventListener:name => listeners.delete(name) }
  class Observer { constructor(fn) { notify = fn } observe() {} disconnect() { disconnected = true } }
  const script = read('docs/.vitepress/theme/PluginRecording.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ recording })', {
    defineProps:() => ({ plugin }), computed:fn => ({ get value() { return fn() } }), ref:value => ({ value:value === null ? refs++ === 0 ? {} : video : value }),
    onMounted:fn => { mount = fn }, onUnmounted:fn => { unmount = fn }, window:intersection ? { IntersectionObserver:Observer } : {}, document:doc, IntersectionObserver:Observer
  })
  mount()
  return { state, video, doc, listeners, unmount, visible(value) { notify?.([{ isIntersecting:value }]) }, get disconnected() { return disconnected } }
}

test('platform recordings use the corresponding local footage and replace simulated feature displays', () => {
  for (const [plugin,file] of [[plugins[0],'domestic-live'],[plugins[1],'global-live']]) {
    const page = mountPluginRecording({ plugin })
    assert.equal(page.state.recording.value.file,file)
    const bytes = readFileSync(resolve(root, `docs/public/videos/${file}-recording.mp4`))
    assert.equal(bytes.toString('ascii',4,8),'ftyp')
    assert.ok(bytes.indexOf(Buffer.from('moov')) < bytes.indexOf(Buffer.from('mdat')))
    assert.ok(bytes.length < 25 * 1024 * 1024)
    assert.ok(existsSync(resolve(root, `docs/public/videos/${file}-poster.webp`)))
    page.unmount()
  }
  const source = read('docs/.vitepress/theme/PluginRecording.vue'), home = read('docs/.vitepress/theme/HomePage.vue')
  assert.match(source, /controls muted playsinline preload="none"/)
  assert.ok(!source.includes('autoplay'))
  assert.ok(!home.includes('PluginFeatureMotion')); assert.ok(!home.includes('plugin-flow'))
  assert.ok(home.includes('<PluginRecording v-else :key="plugins[previewPlugin].id"'))
  assert.ok(source.includes('各格取自不同时间段'))
})

test('recordings pause offscreen, in the background and when switching plugins', () => {
  const page = mountPluginRecording()
  page.visible(true); assert.equal(page.video.paused,false)
  page.visible(false); assert.equal(page.video.paused,true)
  page.video.paused = false; page.doc.hidden = true; page.listeners.get('visibilitychange')(); assert.equal(page.video.paused,true)
  page.doc.hidden = false; page.listeners.get('visibilitychange')(); assert.equal(page.video.paused,true, 'returning must preserve a paused recording')
  page.video.paused = false; page.unmount(); assert.equal(page.video.paused,true); assert.ok(page.disconnected); assert.equal(page.listeners.size,0)
  const fallback = mountPluginRecording({ intersection:false })
  fallback.doc.hidden = true; fallback.listeners.get('visibilitychange')(); assert.equal(fallback.video.paused,true); fallback.unmount()
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


function mountMatchTimeline({ reduced = false, intersection = true, ready = true } = {}) {
  const listeners = new Map()
  let mount, unmount, notify, refs = 0, disconnected = false, loads = 0
  const video = { currentTime:0, readyState:ready ? 1 : 0, paused:true, play() { this.paused = false; return Promise.resolve() }, pause() { this.paused = true }, load() { loads++ } }
  const media = { matches:reduced, addEventListener:(name,fn) => listeners.set('media:' + name,fn), removeEventListener:name => listeners.delete('media:' + name) }
  const doc = { hidden:false, addEventListener:(name,fn) => listeners.set(name,fn), removeEventListener:name => listeners.delete(name) }
  class Observer { constructor(fn) { notify = fn } observe() {} disconnect() { disconnected = true } }
  const script = read('docs/.vitepress/theme/MatchSyncTimeline.vue').split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
  const state = runInNewContext(script + '\n;({ chapters, duration, time, active, paused, reduced, playing, clock, sync, seek, jump, togglePlayback, hold, onFocus, onBlur, onLoaded, onTimeUpdate, onEnded })', {
    computed:fn => ({ get value() { return fn() } }), ref:value => ({ value:value === null ? refs++ === 0 ? {} : video : value }),
    onMounted:fn => { mount = fn }, onUnmounted:fn => { unmount = fn }, window:{ matchMedia:() => media, ...(intersection ? { IntersectionObserver:Observer } : {}) }, document:doc, IntersectionObserver:Observer
  })
  mount()
  return { state, video, listeners, media, doc, unmount, visible(value) { notify?.([{ isIntersecting:value }]) }, get loads() { return loads }, get disconnected() { return disconnected } }
}

test('the recorded match timeline seeks the actual video and restores pending seeks after metadata', () => {
  const page = mountMatchTimeline(), s = page.state
  assert.equal(s.duration,28); assert.equal(s.chapters.length,4)
  for (const [time,index] of [[0,0],[5.9,0],[6,1],[15.9,1],[16,2],[22.9,2],[23,3],[28,3]]) {
    s.seek(time); assert.equal(s.active.value,index); assert.equal(page.video.currentTime,time)
  }
  s.seek(-100); assert.equal(s.time.value,0)
  s.seek(100); assert.equal(s.time.value,28)
  s.jump(2); assert.equal(s.time.value,16); assert.equal(page.video.currentTime,16)
  s.jump(99); assert.equal(s.time.value,16)
  page.video.currentTime = 17.25; s.onTimeUpdate(); assert.equal(s.time.value,17.25)
  assert.equal(s.clock(28),'0:28')
  page.unmount()
  const pending = mountMatchTimeline({ ready:false })
  pending.state.seek(10); assert.equal(pending.loads,1); assert.equal(pending.video.currentTime,0)
  pending.video.readyState = 1; pending.state.onLoaded(); assert.equal(pending.video.currentTime,10)
  pending.unmount()
})

test('native video playback pauses offscreen, during seeking, on focus and in the background', () => {
  const page = mountMatchTimeline(), s = page.state
  assert.equal(page.video.paused,true); page.visible(true); assert.equal(page.video.paused,false)
  s.hold(true); assert.equal(page.video.paused,true); s.seek(10.5); assert.equal(page.video.currentTime,10.5)
  s.hold(false); assert.equal(page.video.paused,false)
  s.onFocus({ target:{ matches:() => true } }); assert.equal(page.video.paused,true)
  s.onBlur({ currentTarget:{ contains:() => false } }); assert.equal(page.video.paused,false)
  s.togglePlayback(); assert.equal(page.video.paused,true)
  s.seek(28); s.togglePlayback(); assert.equal(s.time.value,0); assert.equal(page.video.currentTime,0)
  page.video.currentTime = 28; s.onEnded(); assert.equal(page.video.currentTime,0); assert.equal(page.video.paused,false)
  page.doc.hidden = true; page.listeners.get('visibilitychange')(); assert.equal(page.video.paused,true)
  page.doc.hidden = false; s.sync(); assert.equal(page.video.paused,false)
  page.visible(false); assert.equal(page.video.paused,true)
  page.visible(true); page.media.matches = true; page.listeners.get('media:change')(); assert.equal(page.video.paused,true)
  page.unmount(); assert.equal(page.video.paused,true); assert.equal(page.listeners.size,0); assert.ok(page.disconnected)
})

test('reduced motion and missing observers preserve manual video chapter navigation', () => {
  for (const options of [{ reduced:true }, { intersection:false }]) {
    const page = mountMatchTimeline(options), s = page.state
    page.visible(true); assert.equal(page.video.paused,true)
    s.jump(3); assert.equal(s.time.value,23); assert.equal(s.active.value,3); assert.equal(page.video.currentTime,23)
    s.seek(9); assert.equal(s.active.value,1)
    if (options.reduced) { s.togglePlayback(); assert.equal(page.video.paused,true) }
    else { s.togglePlayback(); assert.equal(page.video.paused,false) }
    page.unmount()
  }
})

test('the match preview contains real local video, matching chapters and no fabricated control overlays', () => {
  const home = readFileSync(resolve(dist,'index.html'),'utf8')
  for (const label of ['对照画面','偏移微调','同步观看','合并弹幕']) assert.ok(home.includes('aria-label="跳转到' + label + '"'))
  assert.match(home, /class="timeline-seek"[^>]*type="range"[^>]*max="28"/)
  assert.match(home, /<video[^>]*match-sync-recording\.mp4[^>]*muted[^>]*playsinline[^>]*preload="none"/)
  assert.ok(home.includes('match-sync-poster.webp'))
  assert.ok(!home.includes('aria-label="设为比赛主画面"'))
  assert.ok(!home.includes('主画面模拟音量'))
  assert.ok(home.includes('真实软件录屏 · 手动微调 · 静音播放'))
  const recording = readFileSync(resolve(root,'docs/public/videos/match-sync-recording.mp4'))
  assert.equal(recording.toString('ascii',4,8),'ftyp')
  assert.ok(recording.indexOf(Buffer.from('moov')) < recording.indexOf(Buffer.from('mdat')), 'the web video supports faststart')
  assert.ok(recording.length < 20 * 1024 * 1024)
})
