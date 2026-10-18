<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const viewport = ref(null)
const paused = ref(false)
let period = 0, frame = 0, last = 0, carry = 0
let visible = false, hovering = false, focused = false, touching = false, pointer = null, navigating = false
let media, observer, sizeObserver, settleTimer
let suppressClick = false
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
  suppressClick = false
  if (event.pointerType !== 'mouse') { touching = true; sync(); return }
  if (event.button !== 0 || event.target.closest('a,button:not(.plugin-demo-trigger)')) return
  pointer = { id: event.pointerId, x: event.clientX, left: viewport.value.scrollLeft }
  sync()
}
function onPointerMove(event) {
  if (!pointer || pointer.id !== event.pointerId) return
  if (Math.abs(event.clientX - pointer.x) < 6) return
  suppressClick = true
  viewport.value.setPointerCapture?.(event.pointerId)
  viewport.value.scrollLeft = pointer.left + pointer.x - event.clientX
}
function onPointerUp(event) {
  if (event.type === 'pointercancel') suppressClick = false
  touching = false
  pointer = null
  viewport.value.releasePointerCapture?.(event.pointerId)
  normalize()
  sync()
}
function onClickCapture(event) {
  if (!suppressClick) return
  suppressClick = false
  event.preventDefault()
  event.stopPropagation()
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
      @scroll.passive="normalize" @dragstart.prevent @click.capture="onClickCapture" @pointerenter="onPointerEnter" @pointerleave="onPointerLeave"
      @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp"
      @keydown.left.prevent="step(-1)" @keydown.right.prevent="step(1)">
      <div v-for="copy in [0,1,2]" :key="copy" class="carousel-group" :aria-hidden="copy !== 1 ? true : undefined"><slot :duplicate="copy !== 1" /></div>
    </div>
    <div class="carousel-controls"><p>拖动浏览 · 点击卡片查看下方功能</p><div>
      <button :aria-label="paused ? '播放插件自动滚动' : '暂停插件自动滚动'" :aria-pressed="paused" @click="togglePlayback"><svg viewBox="0 0 24 24" aria-hidden="true"><path v-if="paused" d="m9 6 9 6-9 6Z" fill="currentColor" stroke="none" /><path v-else d="M9 7v10m6-10v10" /></svg></button>
      <button aria-label="上一个插件" @click="step(-1)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6" /></svg></button><button aria-label="下一个插件" @click="step(1)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6" /></svg></button>
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
.carousel-controls button { display:grid; place-items:center; width:36px; height:36px; border:0; border-radius:50%; background:var(--button-surface); color:var(--vp-c-text-1); transition:background-color 160ms ease,transform 120ms var(--ease-out); }
.carousel-controls svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:2.4; stroke-linecap:round; stroke-linejoin:round; }
.carousel-controls button:active { transform:scale(.94); }
@media (hover:hover) and (pointer:fine) { .carousel-controls button:hover { background:var(--button-surface-hover); } }
@media (max-width:700px) { .plugin-carousel { --plugin-card-width:calc(100vw - 82px); } .carousel-viewport,.carousel-group { gap:16px; } .carousel-controls { gap:8px; } .carousel-controls p { max-width:130px; } }
</style>
