<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'

// 56 格铺满并超出视口，拖动才有"能继续看"的空间。
const tiles = 56
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

const MAGNIFY_SCALE = 0.45
const MAGNIFY_RADIUS_MAX = 220

function applyTransform() {
  const element = wall.value
  if (!element) return
  const sink = reduced ? 0 : scrollProgress * 26
  const scale = reduced ? 1 : 1 - scrollProgress * 0.16
  element.style.transform =
    `translate3d(calc(-50% + ${offset.x.toFixed(1)}px), calc(-50% + ${offset.y.toFixed(1)}px + ${sink.toFixed(2)}vh), 0) scale(${scale.toFixed(3)})`
  element.style.opacity = (1 - scrollProgress * 0.72).toFixed(3)
}

// 每格按指针距离放大，越近越大，像望远镜扫过画面墙。
function magnify(clientX, clientY) {
  const element = wall.value
  if (!element || !tileNodes.length) return
  const rect = element.getBoundingClientRect()
  const px = clientX - rect.left
  const py = clientY - rect.top
  const radius = Math.min(MAGNIFY_RADIUS_MAX, rect.width / 4)
  for (const tile of tileNodes) {
    const cx = tile.offsetLeft + tile.offsetWidth / 2
    const cy = tile.offsetTop + tile.offsetHeight / 2
    const distance = Math.hypot(px - cx, py - cy)
    const near = Math.max(0, 1 - distance / radius)
    const scale = 1 + MAGNIFY_SCALE * near * near
    const next = near > 0.002 ? `scale(${scale.toFixed(3)})` : ''
    // 只有靠近指针的那一格才亮起来，邻格只留一点缩放。
    const lit = near > 0.4
    const key = next + (lit ? '!' : '')
    // 只在真正变化时写样式，静止的指针不会反复触发布局。
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
  if (event.button !== 0 && event.pointerType === 'mouse') return
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
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', measure, { passive: true })
})

onUnmounted(() => {
  cancelAnimationFrame(scrollFrame)
  cancelAnimationFrame(pointerFrame)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', measure)
})
</script>

<template>
  <section ref="section" class="splash" aria-hidden="true"
    @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp"
    @pointercancel="onPointerUp" @pointerleave="onPointerLeave">
    <div ref="wall" class="splash-wall">
      <span v-for="n in tiles" :key="n" class="splash-tile" :style="{ '--i': (n - 1) % 12 }"></span>
    </div>
    <p class="splash-hint">拖动查看<span class="splash-dot">·</span>向下滚动</p>
  </section>
</template>

<style scoped>
.splash {
  position: sticky;
  top: var(--vp-nav-height);
  z-index: 0;
  height: calc(100vh - var(--vp-nav-height));
  height: calc(100dvh - var(--vp-nav-height));
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
  width: max(142vw, 620px);
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: clamp(8px, 1vw, 16px);
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
  /* 入场位移走独立的 translate，transform 留给指针放大，两者不会互相覆盖。 */
  translate: 0 0;
  animation: tile-rise 760ms var(--ease-out) both;
  animation-delay: calc(var(--i) * 45ms);
}
@keyframes tile-rise {
  from { opacity: 0; translate: 0 26px; }
  to { opacity: 1; translate: 0 0; }
}
.splash-hint {
  position: absolute;
  left: 50%;
  bottom: 5.5vh;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--vp-c-text-2);
  font-size: 13px;
  letter-spacing: .1em;
  pointer-events: none;
  animation: hint-in 600ms 780ms var(--ease-out) both;
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
