<script setup>
import { withBase } from 'vitepress'
import { onMounted, onUnmounted, reactive, ref } from 'vue'

// 标准画面墙：等尺寸格子铺成 2×2 的重复块，拖动按周期取模，四向都接得上。
const TILE_COUNT = 56
const REPEATS = 4
const TOTAL_TILES = TILE_COUNT * REPEATS
// 从小键盘 5 开始按 6、3、2、1、4、7、8、9 的方向由内向外绕圈。
const RING_OFFSETS = [
  [0, 0], [1, 0], [1, 1], [0, 1], [-1, 1],
  [-1, 0], [-1, -1], [0, -1], [1, -1],
  [2, -1], [2, 0], [2, 1], [2, 2], [1, 2],
  [0, 2], [-1, 2], [-2, 2], [-2, 1], [-2, 0], [-2, -1],
  [-2, -2], [-1, -2], [0, -2], [1, -2], [2, -2],
  [3, -2], [3, -1], [3, 0], [3, 1],
  [3, 2], [3, 3], [2, 3], [1, 3], [0, 3], [-1, 3], [-2, 3],
  [-3, 3], [-3, 2], [-3, 1], [-3, 0], [-3, -1], [-3, -2], [-3, -3],
  [-2, -3], [-1, -3], [0, -3], [1, -3], [2, -3], [3, -3],
  // 每份只有 7 列，外圈接到下方空行，避免横向周期覆盖。
  [3, 4], [2, 4], [1, 4], [0, 4], [-1, 4], [-2, 4]
]
const base = withBase('/')
const section = ref(null)
const wall = ref(null)
const offset = reactive({ x: 0, y: 0 })
const columns = ref(14)
const imageTiles = ref(buildImageTiles(14))
let tileNodes = []
let period = { x: 0, y: 0 }
let centerShift = { x: 0, y: 0 }
let scrollProgress = 0
let reduced = false
let dragging = false
let dragStart = { x: 0, y: 0, ox: 0, oy: 0 }
// 触摸按下的起点；超过横向阈值才转为拖动，纵向手势继续滚页面。
let pendingDrag = null
let scrollFrame = 0
let scrollPending = false
let pointerFrame = 0
let pointerAt = null
let driftFrame = 0
let driftX = 0
let driftY = 0
let navRevealed = false

const MAGNIFY_SCALE = 0.45

// 把位移折回 [-p/2, p/2)，内容本来重复，折回去看不出接缝。
function wrap(value, p) {
  if (!p) return value
  return (((value + p / 2) % p) + p) % p - p / 2
}

// 同一组画面在四个重复象限各放一份，拖动折回时内容仍能接上。
function buildImageTiles(gridColumns) {
  const rows = TOTAL_TILES / gridColumns
  const repeatColumns = gridColumns / 2
  const repeatRows = TILE_COUNT / repeatColumns
  // 手机每份只有 4 列，外圈左右会重叠，剩余画面依次接到上下空行。
  const ringOffsets = repeatColumns === 4
    ? [...RING_OFFSETS.slice(0, 16), [-1, -2], [0, -2], [1, -2], [2, -2],
      [2, 3], [1, 3], [0, 3], [-1, 3], [-1, -3], [0, -3], [1, -3], [2, -3], [2, 4],
      [1, 4], [0, 4], [-1, 4], [-1, -4], [0, -4], [1, -4], [2, -4],
      [2, 5], [1, 5], [0, 5], [-1, 5], [-1, -5], [0, -5], [1, -5], [2, -5],
      [2, 6], [1, 6], [0, 6], [-1, 6], [-1, -6], [0, -6], [1, -6], [2, -6],
      [2, 7], [1, 7], [0, 7]]
    : RING_OFFSETS
  const centerRow = rows / 2
  const centerColumn = gridColumns / 2
  const tiles = new Map()
  for (let quadrantRow = 0; quadrantRow < 2; quadrantRow += 1) {
    for (let quadrantColumn = 0; quadrantColumn < 2; quadrantColumn += 1) {
      ringOffsets.forEach(([x, y], index) => {
        const row = (centerRow - 1 + quadrantRow * repeatRows + y + rows) % rows
        const column = (centerColumn - 1 + quadrantColumn * repeatColumns + x + gridColumns) % gridColumns
        tiles.set(row * gridColumns + column + 1, index + 1)
      })
    }
  }
  return tiles
}

function tileImage(n) {
  return imageTiles.value.get(n)
}

function applyTransform() {
  const element = wall.value
  if (!element) return
  const sink = reduced ? 0 : scrollProgress * 26
  const scale = reduced ? 1 : 1 - scrollProgress * 0.16
  element.style.transform =
    `translate3d(calc(-50% + ${(offset.x + driftX + centerShift.x).toFixed(1)}px), ` +
    `calc(-50% + ${(offset.y + driftY + centerShift.y).toFixed(1)}px + ${sink.toFixed(2)}vh), 0) scale(${scale.toFixed(3)})`
  element.style.opacity = (1 - scrollProgress * 0.72).toFixed(3)
}

// 没人操作时整面墙自己缓缓游动，双频叠加让轨迹不重复。
function drift(now) {
  // 开屏墙在手机上铺满第一屏，若继续游动会一直重绘，滚动后停手。
  const active = !dragging && !reduced && !navRevealed && (window.scrollY || 0) < (window.innerHeight || 1) * 0.6
  if (active) {
    driftX = Math.sin(now / 2600) * 62 + Math.sin(now / 7100) * 22
    driftY = Math.cos(now / 3300) * 44 + Math.cos(now / 8300) * 16
    applyTransform()
  }
  driftFrame = requestAnimationFrame(drift)
}

function magnify(clientX, clientY) {
  const element = wall.value
  // 内容已经压上来时，这面墙只是背景，不再跟着鼠标放大。
  if (navRevealed || !element || !tileNodes.length) return
  const rect = element.getBoundingClientRect()
  const px = clientX - rect.left
  const py = clientY - rect.top
  const radius = Math.max(90, Math.min(240, rect.width / 10))
  for (const tile of tileNodes) {
    const cx = tile.offsetLeft + tile.offsetWidth / 2
    const cy = tile.offsetTop + tile.offsetHeight / 2
    const near = Math.max(0, 1 - Math.hypot(px - cx, py - cy) / radius)
    const scale = 1 + MAGNIFY_SCALE * near * near
    const next = near > 0.002 ? `scale(${scale.toFixed(3)})` : ''
    const lit = near > 0.4
    const key = next + (lit ? '!' : '')
    if (tile.__scale !== key) {
      tile.__scale = key
      tile.style.transform = next
      tile.style.zIndex = next ? '2' : ''
      tile.style.boxShadow = lit ? '0 20px 46px #000a' : ''
      tile.style.borderColor = lit ? 'var(--vp-c-brand-1)' : ''
    }
  }
}

function resetMagnify() {
  for (const tile of tileNodes) {
    if (tile.__scale === '') continue
    tile.__scale = ''
    tile.style.transform = ''
    tile.style.zIndex = ''
    tile.style.boxShadow = ''
    tile.style.borderColor = ''
  }
}

function measure() {
  const element = wall.value
  if (!element) return
  const styles = getComputedStyle(element)
  const gridColumns = styles.gridTemplateColumns.split(/\s+/).filter(Boolean).length
  if (gridColumns && gridColumns !== columns.value) {
    columns.value = gridColumns
    imageTiles.value = buildImageTiles(gridColumns)
  }
  const gap = parseFloat(styles.gap) || 16
  const rows = TOTAL_TILES / columns.value
  // 墙面由 2×2 份相同内容拼成，位移的重复周期就是其中一份的边长。
  period = {
    x: (element.offsetWidth + gap) / 2,
    y: (element.offsetHeight + gap) / 2
  }
  // 偶数列的中心落在两格之间，补半格让第 5 格真正对齐屏幕中心。
  centerShift = {
    x: (element.offsetWidth + gap) / (columns.value * 2),
    y: (element.offsetHeight + gap) / (rows * 2)
  }
  offset.x = wrap(offset.x, period.x)
  offset.y = wrap(offset.y, period.y)
  applyTransform()
}

function updateScroll() {
  const viewport = window.innerHeight || 1
  scrollProgress = reduced ? 0 : Math.min(1, Math.max(0, (window.scrollY || 0) / (viewport * 0.85)))
  applyTransform()
  // 手机上首屏就是整面墙，顶栏若一直藏着就没有导航入口，所以一滑动就放出来。
  const threshold = viewport <= 700 ? 4 : Math.min(140, viewport * 0.14)
  const reveal = (window.scrollY || 0) > threshold
  if (reveal !== navRevealed) {
    navRevealed = reveal
    document.documentElement.classList.toggle('splash-revealed', reveal)
    // 一进入内容层就把残留的放大收回去，滚回顶部再恢复。
    if (reveal) resetMagnify()
  }
  // 滚动量很小时只改墙的深浅，不移动、不淡出，避免手机首屏墙被压没。
  document.documentElement.classList.toggle('splash-scrolled', (window.scrollY || 0) > 4)
}

function onScroll() {
  if (scrollPending) return
  scrollPending = true
  scrollFrame = requestAnimationFrame(() => {
    scrollPending = false
    updateScroll()
  })
}

function onPointerDown(event) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  // 触摸纵向留给滚动：按下时只记起点，判定为横向拖动后才真正接管。
  if (event.pointerType !== 'mouse') {
    pendingDrag = { id: event.pointerId, x: event.clientX, y: event.clientY }
    return
  }
  dragging = true
  dragStart = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y }
  section.value?.classList.add('is-dragging')
  wall.value?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event) {
  if (pendingDrag && !dragging && event.pointerId === pendingDrag.id) {
    const dx = Math.abs(event.clientX - pendingDrag.x)
    const dy = Math.abs(event.clientY - pendingDrag.y)
    if (dx < 8 || dx < dy * 1.2) return
    dragging = true
    dragStart = { x: pendingDrag.x, y: pendingDrag.y, ox: offset.x, oy: offset.y }
    pendingDrag = null
    section.value?.classList.add('is-dragging')
    wall.value?.setPointerCapture?.(event.pointerId)
  }
  if (!dragging && navRevealed) return
  pointerAt = { x: event.clientX, y: event.clientY }
  if (!pointerFrame) {
    pointerFrame = requestAnimationFrame(() => {
      pointerFrame = 0
      if (pointerAt) magnify(pointerAt.x, pointerAt.y)
    })
  }
  if (!dragging) return
  offset.x = wrap(dragStart.ox + (event.clientX - dragStart.x), period.x)
  offset.y = wrap(dragStart.oy + (event.clientY - dragStart.y), period.y)
  applyTransform()
}

function onPointerUp(event) {
  pendingDrag = null
  dragging = false
  section.value?.classList.remove('is-dragging')
  wall.value?.releasePointerCapture?.(event.pointerId)
}

function onPointerLeave() {
  pointerAt = null
  resetMagnify()
}

onMounted(() => {
  reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  tileNodes = wall.value ? [...wall.value.children] : []
  measure()
  updateScroll()
  const root = document.documentElement
  root.classList.add('splash-mode', 'splash-boot')
  requestAnimationFrame(() => root.classList.remove('splash-boot'))
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', measure, { passive: true })
  if (!reduced) driftFrame = requestAnimationFrame(drift)
})

onUnmounted(() => {
  cancelAnimationFrame(scrollFrame)
  cancelAnimationFrame(pointerFrame)
  cancelAnimationFrame(driftFrame)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', measure)
  document.documentElement.classList.remove('splash-mode', 'splash-boot', 'splash-revealed')
})
</script>

<template>
  <section ref="section" class="splash" aria-hidden="true"
    @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp"
    @pointercancel="onPointerUp" @pointerleave="onPointerLeave">
    <div ref="wall" class="splash-wall">
      <span v-for="n in TILE_COUNT * REPEATS" :key="n" class="splash-tile" :style="{ '--i': (n - 1) % 14 }">
        <img v-if="tileImage(n)" :src="`${base}wall/${String(tileImage(n)).padStart(2, '0')}.webp`"
          alt="" width="1280" height="720" draggable="false" />
      </span>
    </div>
    <p class="splash-hint"><span class="hint-copy"><span>拖动查看</span><span class="splash-dot">·</span><span>向下滚动进入主页</span></span></p>
  </section>
</template>

<style scoped>
.splash {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  cursor: grab;
  user-select: none;
  /* 手机上纵向手势交给页面滚动，否则整屏墙会吃掉进入主页的滑动；横向仍可拖动墙。 */
  touch-action: pan-y;
  background: radial-gradient(120% 85% at 50% 0%, color-mix(in srgb, var(--vp-c-brand-1) 15%, transparent), transparent 68%);
}
.splash.is-dragging { cursor: grabbing; }
.splash-wall {
  position: absolute;
  left: 50%;
  top: 50%;
  width: max(340vw, 2480px);
  display: grid;
  /* 2 份相同内容并排、上下也各 2 份，拖到周期处折回去就是无缝的。 */
  grid-template-columns: repeat(14, minmax(0, 1fr));
  gap: clamp(8px, 0.9vw, 16px);
  transform: translate3d(-50%, -50%, 0);
  transform-origin: 50% 50%;
  will-change: transform, opacity;
}
.splash-tile {
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border-radius: 10px;
  border: 1px solid var(--vp-c-divider);
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--vp-c-brand-1) 16%, transparent), transparent 62%),
    var(--vp-c-bg-alt);
  box-shadow: inset 0 1px 0 #ffffff10;
  transform-origin: 50% 50%;
  transition: transform 110ms var(--ease-out), border-color 160ms ease, box-shadow 160ms ease;
  /* 入场位移走独立的 translate，transform 留给指针放大。 */
  translate: 0 0;
  animation: tile-rise 760ms var(--ease-out) both;
  animation-delay: calc(var(--i) * 55ms);
}
.splash-tile img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  pointer-events: none;
}
@keyframes tile-rise {
  from { opacity: 0; translate: 0 26px; }
  to { opacity: 1; translate: 0 0; }
}
.splash-hint {
  position: absolute;
  left: 50%;
  bottom: 6vh;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  max-width: calc(100% - 32px);
  padding: 12px 18px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 18px;
  background: var(--vp-c-bg-soft);
  margin: 0;
  color: var(--vp-c-text-1);
  font-size: 13px;
  letter-spacing: .1em;
  pointer-events: none;
  animation: hint-in 250ms 900ms var(--ease-out) both;
}
.hint-copy { display: flex; align-items: center; justify-content: center; gap: 8px; white-space: nowrap; animation: letter-reveal 350ms 1150ms var(--ease-out) both; }
.splash-dot { opacity: .5; }
@keyframes letter-reveal { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: translateY(0); } }
@keyframes hint-in { from { opacity: 0; } to { opacity: 1; } }
@media (max-width: 700px) {
  .splash-wall { grid-template-columns: repeat(8, minmax(0, 1fr)); }
  .splash-hint { padding: 11px 12px; font-size: 11px; letter-spacing: .04em; }
  .hint-copy { gap: 5px; }
}
@media (prefers-reduced-motion: reduce) {
  .splash-tile { animation: none; transition: none; }
  .splash-hint, .hint-copy { animation: none; }
}
</style>
