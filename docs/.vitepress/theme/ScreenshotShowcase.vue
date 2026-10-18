<script setup>
import { withBase } from 'vitepress'
import { onMounted, onUnmounted, ref } from 'vue'

const shots = [
  { file: 'landscape.webp', label: '横屏观看', caption: '一个主画面，多路小画面。喜欢的直播，各有位置。' },
  { file: 'danmaku.webp', label: '弹幕布局', caption: '直播与聊天同屏，弹幕跟随主画面。' },
  { file: 'portrait.webp', label: '竖屏布局', caption: '关注移到顶部，画面纵向展开。' }
]
const active = ref(0)
const story = ref(null)
const pin = ref(null)
const product = ref(null)
const productHeight = ref(0)
const enhanced = ref(false)
const progress = ref(1)
const controlsReady = ref(false)
const paused = ref(false)
const revealing = ref(false)
let triggerHeight = 0
let motion, observer, sizeObserver, timer, frame = 0, visible = false, interacting = false

function stop() { clearTimeout(timer); timer = undefined }
function schedule() {
  stop()
  if (!enhanced.value || !visible || paused.value || interacting || motion?.matches || document.hidden || (!controlsReady.value && progress.value < .65)) return
  timer = setTimeout(() => selectShot((active.value + 1) % shots.length), 5000)
}
function selectShot(index) {
  if (index !== active.value) revealing.value = !motion?.matches
  active.value = index
  schedule()
}
function togglePlayback() { paused.value = !paused.value; schedule() }
function interact(value) { interacting = value; schedule() }
function update() {
  frame = 0
  if (!enhanced.value || !story.value || !pin.value) return
  const top = parseFloat(getComputedStyle(pin.value).top) || 0
  const centerStart = Math.max(top, (window.innerHeight - triggerHeight) / 2)
  const range = story.value.offsetHeight - pin.value.offsetHeight
  progress.value = Math.min(1, Math.max(0, (centerStart - story.value.getBoundingClientRect().top) / Math.max(1, range * .7)))
  if (progress.value >= .95) controlsReady.value = true
  else if (progress.value <= .15) controlsReady.value = false
  schedule()
}
function onScroll() { if (!frame) frame = requestAnimationFrame(update) }
function preferenceChanged() {
  enhanced.value = !motion.matches && !!observer && !!sizeObserver
  if (!enhanced.value) progress.value = 1
  if (motion.matches) revealing.value = false
  update()
  schedule()
}
onMounted(() => {
  motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; schedule() }, { threshold: .15 })
    observer.observe(pin.value)
  }
  if ('ResizeObserver' in window) {
    sizeObserver = new ResizeObserver(() => {
      productHeight.value = product.value.offsetHeight
      if (!triggerHeight || active.value === 0) triggerHeight = productHeight.value
    })
    sizeObserver.observe(product.value)
  }
  preferenceChanged()
  motion.addEventListener('change', preferenceChanged)
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll, { passive: true })
  document.addEventListener('visibilitychange', schedule)
})
onUnmounted(() => {
  stop()
  cancelAnimationFrame(frame)
  observer?.disconnect()
  sizeObserver?.disconnect()
  motion?.removeEventListener('change', preferenceChanged)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
  document.removeEventListener('visibilitychange', schedule)
})
</script>

<template>
  <section ref="story" class="screenshot-story" :class="{ 'is-enhanced': enhanced }" aria-label="真实软件截图"
    :style="{ '--shot-scale': enhanced ? 1 - progress * .18 : 1, '--product-height': productHeight + 'px', '--frame-ratio': active === 2 ? 9 / 16 : 16 / 9, '--selected': active, '--controls': controlsReady || !enhanced ? 1 : Math.min(1, Math.max(0, (progress - .55) / .4)) }">
    <div ref="pin" class="screenshot-pin">
      <div class="product-wrap" :class="{ 'is-measured': enhanced && productHeight > 0 }" :style="enhanced && productHeight > 0 ? { height: productHeight * (1 - progress * .18) + 'px' } : undefined">
      <div ref="product" class="hero-product" :class="{ 'is-portrait': active === 2 }">
        <div class="window-label"><img :src="withBase('/logo.svg')" width="24" height="24" alt="" /><span>DD 监控室 CE</span><span class="window-mode">多画面 · 自在观看</span></div>
        <div class="screenshot-stage">
            <img class="hero-shot" :src="withBase('/screenshots/' + shots[active].file)"
              :alt="shots[active].caption" width="1920" height="1080" fetchpriority="high" />
          <div v-if="revealing" :key="active" class="pixel-wipe" aria-hidden="true" @animationend.self="revealing = false">
            <span v-for="i in 96" :key="i" :style="{ '--delay': ((i - 1) % 12 + Math.floor((i - 1) / 12)) * 12 + 'ms' }"></span>
          </div>
          <button class="gallery-playback" :aria-label="paused ? '播放截图轮播' : '暂停截图轮播'" :aria-pressed="paused" @click="togglePlayback">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path v-if="paused" d="m8 5 11 7-11 7Z" /><path v-else d="M7 5h4v14H7zm6 0h4v14h-4z" /></svg>
          </button>
        </div>
      </div>
      </div>
      <div class="gallery-controls" :inert="enhanced && !controlsReady && progress < .65" @pointerenter="interact(true)" @pointerleave="interact(false)"
        @focusin="interact(true)" @focusout="interact(false)">
        <div class="gallery-tabs" role="group" aria-label="截图布局">
          <span class="gallery-selection" aria-hidden="true"></span>
          <button v-for="(shot, i) in shots" :key="shot.file" :aria-pressed="active === i" @click="selectShot(i)">{{ shot.label }} <span class="layout-ratio" aria-hidden="true">{{ i === 2 ? '9:16' : '16:9' }}</span></button>
        </div>
        <p class="product-caption gallery-caption">{{ shots[active].caption }}</p>
        <p class="screenshot-source">截图来自现有项目文档，不同版本的界面细节可能有所调整。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.screenshot-story { grid-column: 1 / -1; min-width: 0; overflow-anchor: none; }
.screenshot-pin { display: grid; grid-template-columns: 180px minmax(0,1fr); align-items: start; gap: 32px; position: relative; }
.product-wrap { grid-column: 2; grid-row: 1; position: relative; width: 100%; }
.product-wrap:has(.is-portrait) { width: min(100%,560px); justify-self: center; }
.product-wrap.is-measured .hero-product { position: absolute; top: 0; left: 0; }
.hero-product { width: 100%; }
.window-label { height: 44px; padding: 0 12px; white-space: nowrap; }
.is-portrait .window-mode { display: none; }
.screenshot-stage { position: relative; aspect-ratio: var(--frame-ratio); }
.gallery-playback { position: absolute; right: 12px; bottom: 12px; display: grid; place-items: center; width: 44px; height: 44px; border-radius: 50%; color: white; background: #101418b3; backdrop-filter: blur(12px); }
.gallery-controls { grid-column: 1; grid-row: 1; position: sticky; top: calc(var(--vp-nav-height) + 24px); width: 100%; }
.gallery-caption { display: block; padding: 0; margin: 16px 0 0; line-height: 1.7; }
.gallery-tabs { position: relative; display: flex; flex-direction: column; gap: 0; padding: 4px; border-radius: 12px; background: var(--vp-c-bg-soft); border: 1px solid var(--vp-c-divider); }
.gallery-tabs button { position: relative; z-index: 1; height: 48px; padding: 8px 12px; border: 0; border-radius: 8px; background: transparent; white-space: nowrap; color: var(--vp-c-text-2); }
.gallery-tabs button[aria-pressed=true] { color: var(--vp-c-bg); background: transparent; }
.layout-ratio { margin-left: 5px; opacity: .6; font-size: 12px; }
.gallery-selection { position: absolute; top: 4px; left: 4px; right: 4px; height: 48px; border-radius: 8px; background: var(--vp-c-text-1); transform: translateY(calc(var(--selected) * 100%)); }
.screenshot-source { margin: 14px 0 0; font-size: 11px; }
.is-enhanced::after { content: ''; display: block; height: 30svh; }
.is-enhanced .screenshot-pin { position: sticky; top: min(calc(var(--vp-nav-height) + 24px), max(var(--vp-nav-height), calc((100svh - var(--product-height)) / 2))); }
.is-enhanced .hero-product { transform: scale(var(--shot-scale)); transform-origin: top center; }
.is-enhanced .gallery-controls { opacity: var(--controls); }
.pixel-wipe { position: absolute; inset: 0; display: grid; grid-template-columns: repeat(12,1fr); grid-template-rows: repeat(8,1fr); pointer-events: none; animation: wipe-end 500ms both; }
.pixel-wipe span { background: var(--vp-c-bg-alt); animation: pixel-clear 260ms var(--ease-out) var(--delay) both; }
@keyframes pixel-clear { from { opacity: 1; transform: scale(1.02); } to { opacity: 0; transform: scale(.6); } }
@keyframes wipe-end { from { visibility: visible; } to { visibility: hidden; } }
@media (prefers-reduced-motion: no-preference) {
  .product-wrap { transition: width 400ms var(--ease-out); }
  .gallery-selection { transition: transform 400ms var(--ease-out); }
}
@media (prefers-reduced-motion: reduce) { .pixel-wipe { display: none; } }
@media (max-width: 700px) {
  .screenshot-pin { grid-template-columns: 84px minmax(0,1fr); gap: 12px; }
  .gallery-tabs button { padding: 8px 3px; font-size: 11px; }
  .layout-ratio,.window-mode { display: none; }
  .gallery-caption { font-size: 11px; }
  .screenshot-source { font-size: 10px; }
  .window-label { height: 34px; padding: 0 6px; gap: 4px; font-size: 10px; }
  .window-label img { width: 18px; height: 18px; }
}
</style>
