<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'

// 标准画面墙：等尺寸格子按网格铺开，整面比视口大，可以拖动查看。
const TILE_COUNT = 56
const section = ref(null)
const wall = ref(null)
const offset = reactive({ x: 0, y: 0 })
let tileNodes = []
let limits = { x: 0, y: 0 }
let scrollProgress = 0
let reduced = false
let dragging = false
let dragStart = { x: 0, y: 0, ox: 0, oy: 0 }
let scrollFrame = 0
let scrollPending = false
let pointerFrame = 0
let pointerAt = null
let driftFrame = 0
let driftX = 0
let driftY = 0
let navRevealed = false

const MAGNIFY_SCALE = 0.45

function applyTransform() {
  const element = wall.value
  if (!element) return
  const sink = reduced ? 0 : scrollProgress * 26
  const scale = reduced ? 1 : 1 - scrollProgress * 0.16
  element.style.transform =
    `translate3d(calc(-50% + ${(offset.x + driftX).toFixed(1)}px), ` +
    `calc(-50% + ${(offset.y + driftY).toFixed(1)}px + ${sink.toFixed(2)}vh), 0) scale(${scale.toFixed(3)})`
  element.style.opacity = (1 - scrollProgress * 0.72).toFixed(3)
}

// 没人操作时整面墙自己缓缓游动，双频叠加让轨迹不重复。
function drift(now) {
  const active = !dragging && !reduced && (window.scrollY || 0) < (window.innerHeight || 1) * 0.6
  if (active) {
    driftX = Math.sin(now / 2600) * 62 + Math.sin(now / 7100) * 22
    driftY = Math.cos(now / 3300) * 44 + Math.cos(now / 8300) * 16
    applyTransform()
  }
  driftFrame = requestAnimationFrame(drift)
}

function magnify(clientX, clientY) {
  const element = wall.value
  if (!element || !tileNodes.length) return
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
  const host = section.value
  if (!element || !host) return
  limits = {
    x: Math.max(0, (element.offsetWidth - host.clientWidth) / 2),
    y: Math.max(0, (element.offsetHeight - host.clientHeight) / 2)
  }
  offset.x = Math.max(-limits.x, Math.min(limits.x, offset.x))
  offset.y = Math.max(-limits.y, Math.min(limits.y, offset.y))
  applyTransform()
}

function updateScroll() {
  const viewport = window.innerHeight || 1
  scrollProgress = reduced ? 0 : Math.min(1, Math.max(0, (window.scrollY || 0) / (viewport * 0.85)))
  applyTransform()
  const reveal = (window.scrollY || 0) > Math.min(140, viewport * 0.14)
  if (reveal !== navRevealed) {
    navRevealed = reveal
    document.documentElement.classList.toggle('splash-revealed', reveal)
  }
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
  dragging = true
  dragStart = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y }
  section.value?.classList.add('is-dragging')
  wall.value?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event) {
  pointerAt = { x: event.clientX, y: event.clientY }
  if (!pointerFrame) {
    pointerFrame = requestAnimationFrame(() => {
      pointerFrame = 0
      if (pointerAt) magnify(pointerAt.x, pointerAt.y)
    })
  }
  if (!dragging) return
  offset.x = Math.max(-limits.x, Math.min(limits.x, dragStart.ox + (event.clientX - dragStart.x)))
  offset.y = Math.max(-limits.y, Math.min(limits.y, dragStart.oy + (event.clientY - dragStart.y)))
  applyTransform()
}

function onPointerUp(event) {
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
      <span v-for="n in TILE_COUNT" :key="n" class="splash-tile" :style="{ '--i': (n - 1) % 14 }"></span>
    </div>
    <p class="splash-hint">拖动查看<span class="splash-dot">·</span>向下滚动进入主页</p>
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
  touch-action: none;
  background: radial-gradient(120% 85% at 50% 0%, color-mix(in srgb, var(--vp-c-brand-1) 15%, transparent), transparent 68%);
}
.splash.is-dragging { cursor: grabbing; }
.splash-wall {
  position: absolute;
  left: 50%;
  top: 50%;
  width: max(170vw, 1240px);
  display: grid;
  /* 列数固定、行数由格子总数决定；窄屏换成 4 列，行数自然变多。 */
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: clamp(8px, 0.9vw, 16px);
  transform: translate3d(-50%, -50%, 0);
  transform-origin: 50% 50%;
  will-change: transform, opacity;
}
.splash-tile {
  aspect-ratio: 16 / 9;
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
  gap: 8px;
  margin: 0;
  color: var(--vp-c-text-2);
  font-size: 13px;
  letter-spacing: .1em;
  pointer-events: none;
  animation: hint-in 700ms 900ms var(--ease-out) both;
}
.splash-dot { opacity: .5; }
@keyframes hint-in { from { opacity: 0; } to { opacity: 1; } }
@media (max-width: 700px) {
  .splash-wall { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .splash-hint { font-size: 12px; }
}
@media (prefers-reduced-motion: reduce) {
  .splash-tile { animation: none; transition: none; }
  .splash-hint { animation: none; }
}
</style>
