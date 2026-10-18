<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const viewport = ref(null)
const paused = ref(false)
let period = 0, frame = 0, last = 0, carry = 0
let visible = false, hovering = false, focused = false, touching = false, pointer = null, navigating = false
let media, observer, sizeObserver, settleTimer
const modulo = (value, size) => ((value % size) + size) % size

function normalize() {
  const element = viewport.value
  if (!period || focused || navigating) return
  if (element.scrollLeft < period || element.scrollLeft >= period * 2)
    element.scrollLeft = period + modulo(element.scrollLeft - period, period)
}
function sync() {
  cancelAnimationFrame(frame)
  frame = 0
  last = 0
  if (visible && period && !paused.value && !hovering && !focused && !touching && !pointer && !navigating && !media?.matches && !document.hidden)
    frame = requestAnimationFrame(tick)
}
function tick(now) {
  if (last) carry += Math.min(now - last, 64) * .028
  last = now
  const pixels = Math.floor(carry)
  if (pixels) { viewport.value.scrollLeft += pixels; carry -= pixels; normalize() }
  frame = requestAnimationFrame(tick)
}
function measure() {
  const element = viewport.value
  const group = element.querySelector('.carousel-group')
  const phase = period ? modulo(element.scrollLeft - period, period) / period : 0
  period = group.offsetWidth + (parseFloat(getComputedStyle(element).columnGap) || 0)
  if (period) element.scrollLeft = period * (1 + phase)
  sync()
}
function togglePlayback() { paused.value = !paused.value; sync() }
function onPointerEnter(event) { if (event.pointerType === 'mouse') { hovering = true; sync() } }
function onPointerLeave() { hovering = false; sync() }
function onFocusIn(event) { focused = event.target.matches(':focus-visible'); sync() }
function onFocusOut(event) {
  if (event.currentTarget.contains(event.relatedTarget)) return
  focused = false
  normalize()
  sync()
}
function step(direction) {
  const element = viewport.value
  const group = element.querySelector('.carousel-group')
  const distance = group.firstElementChild.offsetWidth + (parseFloat(getComputedStyle(group).columnGap) || 0)
  clearTimeout(settleTimer)
  navigating = true
  sync()
  element.scrollBy({ left: direction * distance, behavior: media?.matches ? 'instant' : 'smooth' })
  settleTimer = setTimeout(() => { navigating = false; normalize(); sync() }, 600)
}
function onPointerDown(event) {
  if (event.pointerType !== 'mouse') { touching = true; sync(); return }
  if (event.button !== 0 || event.target.closest('a,button')) return
  pointer = { id: event.pointerId, x: event.clientX, left: viewport.value.scrollLeft }
  sync()
}
function onPointerMove(event) {
  if (!pointer || pointer.id !== event.pointerId) return
  if (Math.abs(event.clientX - pointer.x) < 6) return
  viewport.value.setPointerCapture?.(event.pointerId)
  viewport.value.scrollLeft = pointer.left + pointer.x - event.clientX
}
function onPointerUp(event) {
  touching = false
  pointer = null
  viewport.value.releasePointerCapture?.(event.pointerId)
  normalize()
  sync()
}
onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  measure()
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; sync() }, { threshold: .1 })
    observer.observe(viewport.value)
  }
  if ('ResizeObserver' in window) {
    sizeObserver = new ResizeObserver(measure)
    sizeObserver.observe(viewport.value.querySelector('.carousel-group'))
  }
  media.addEventListener('change', sync)
  document.addEventListener('visibilitychange', sync)
  window.addEventListener('resize', measure, { passive: true })
})
onUnmounted(() => {
  cancelAnimationFrame(frame)
  clearTimeout(settleTimer)
  observer?.disconnect()
  sizeObserver?.disconnect()
  media?.removeEventListener('change', sync)
  document.removeEventListener('visibilitychange', sync)
  window.removeEventListener('resize', measure)
})
</script>

<template>
  <div class="plugin-carousel" aria-label="插件横向循环展示" @focusin="onFocusIn" @focusout="onFocusOut">
    <div ref="viewport" class="carousel-viewport" tabindex="0" role="group" aria-label="左右拖动浏览插件"
      @scroll.passive="normalize" @dragstart.prevent @pointerenter="onPointerEnter" @pointerleave="onPointerLeave"
      @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp"
      @keydown.left.prevent="step(-1)" @keydown.right.prevent="step(1)">
      <div v-for="copy in [0,1,2]" :key="copy" class="carousel-group" :aria-hidden="copy !== 1 ? true : undefined"><slot :duplicate="copy !== 1" /></div>
    </div>
    <div class="carousel-controls"><p>拖动浏览，发现更多观看方式</p><div>
      <button :aria-label="paused ? '播放插件自动滚动' : '暂停插件自动滚动'" :aria-pressed="paused" @click="togglePlayback">{{ paused ? '▶' : 'Ⅱ' }}</button>
      <button aria-label="上一个插件" @click="step(-1)">←</button><button aria-label="下一个插件" @click="step(1)">→</button>
    </div></div>
  </div>
</template>

<style scoped>
.plugin-carousel { --plugin-card-width: clamp(280px,42vw,560px); min-width:0; }
.carousel-viewport { display:flex; gap:24px; overflow-x:auto; padding:6px 2px 12px; scrollbar-width:none; overscroll-behavior-x:contain; cursor:grab; touch-action:pan-x pan-y; user-select:none; }
.carousel-viewport::-webkit-scrollbar { display:none; }
.carousel-viewport:active { cursor:grabbing; }
.carousel-group { display:flex; flex:none; gap:24px; }
.carousel-controls { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-top:14px; }
.carousel-controls p { font-size:12px; color:var(--vp-c-text-2); }
.carousel-controls > div { display:flex; gap:8px; }
.carousel-controls button { display:grid; place-items:center; width:38px; height:38px; border:1px solid var(--vp-c-divider); border-radius:50%; background:var(--vp-c-bg-soft); font-size:16px; }
@media (hover:hover) and (pointer:fine) { .carousel-controls button:hover { border-color:var(--vp-c-brand-1); color:var(--vp-c-brand-1); } }
@media (max-width:700px) { .plugin-carousel { --plugin-card-width:calc(100vw - 82px); } .carousel-viewport,.carousel-group { gap:16px; } .carousel-controls { gap:8px; } .carousel-controls p { max-width:130px; } }
</style>
