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
let motion, observer, sizeObserver, timer, frame = 0, visible = false, interacting = false

function stop() { clearTimeout(timer); timer = undefined }
function schedule() {
  stop()
  if (!enhanced.value || !visible || paused.value || interacting || motion?.matches || document.hidden || (!controlsReady.value && progress.value < .65)) return
  timer = setTimeout(() => { active.value = (active.value + 1) % shots.length; schedule() }, 5000)
}
function selectShot(index) { active.value = index; schedule() }
function togglePlayback() { paused.value = !paused.value; schedule() }
function interact(value) { interacting = value; schedule() }
function update() {
  frame = 0
  if (!enhanced.value || !story.value || !pin.value) return
  const top = parseFloat(getComputedStyle(pin.value).top) || 0
  const range = story.value.offsetHeight - pin.value.offsetHeight
  progress.value = Math.min(1, Math.max(0, (top - story.value.getBoundingClientRect().top) / Math.max(1, range * .7)))
  if (progress.value >= .95) controlsReady.value = true
  else if (progress.value <= .15) controlsReady.value = false
  schedule()
}
function onScroll() { if (!frame) frame = requestAnimationFrame(update) }
function preferenceChanged() {
  enhanced.value = !motion.matches && !!observer && !!sizeObserver
  if (!enhanced.value) progress.value = 1
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
    sizeObserver = new ResizeObserver(() => { productHeight.value = product.value.offsetHeight })
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
    :style="{ '--shot-scale': enhanced ? 1 - progress * .18 : 1, '--frame-ratio': active === 2 ? 9 / 16 : 16 / 9, '--selected': active, '--controls': controlsReady || !enhanced ? 1 : Math.min(1, Math.max(0, (progress - .55) / .4)) }">
    <div ref="pin" class="screenshot-pin">
      <div class="product-wrap" :class="{ 'is-measured': enhanced && productHeight > 0 }" :style="enhanced && productHeight > 0 ? { height: productHeight * (1 - progress * .18) + 'px' } : undefined">
      <div ref="product" class="hero-product">
        <div class="window-label"><img :src="withBase('/logo.svg')" width="24" height="24" alt="" /><span>DD 监控室 CE</span><span class="window-mode">多画面 · 自在观看</span></div>
        <div class="screenshot-stage">
          <Transition name="screenshot">
            <img :key="shots[active].file" class="hero-shot" :src="withBase('/screenshots/' + shots[active].file)"
              :alt="shots[active].caption" width="1920" height="1080" fetchpriority="high" />
          </Transition>
          <button class="gallery-playback" :aria-label="paused ? '播放截图轮播' : '暂停截图轮播'" :aria-pressed="paused" @click="togglePlayback">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path v-if="paused" d="m8 5 11 7-11 7Z" /><path v-else d="M7 5h4v14H7zm6 0h4v14h-4z" /></svg>
          </button>
        </div>
        <div class="product-caption">{{ shots[active].caption }}</div>
      </div>
      </div>
      <div class="gallery-controls" :inert="enhanced && !controlsReady && progress < .65" @pointerenter="interact(true)" @pointerleave="interact(false)"
        @focusin="interact(true)" @focusout="interact(false)">
        <div class="gallery-tabs" role="group" aria-label="截图布局">
          <span class="gallery-selection" aria-hidden="true"></span>
          <button v-for="(shot, i) in shots" :key="shot.file" :aria-pressed="active === i" @click="selectShot(i)">{{ shot.label }} <span class="layout-ratio" aria-hidden="true">{{ i === 2 ? '9:16' : '16:9' }}</span></button>
        </div>
        <p class="screenshot-source">截图来自现有项目文档，不同版本的界面细节可能有所调整。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.screenshot-story { grid-column: 1 / -1; min-width: 0; overflow-anchor: none; }
.screenshot-pin { display: flex; flex-direction: column; align-items: center; gap: 24px; }
.product-wrap { position: relative; width: min(100%, calc((100svh - var(--vp-nav-height) - 220px) * var(--frame-ratio))); }
.product-wrap.is-measured .hero-product { position: absolute; top: 0; left: 0; }
.hero-product { width: 100%; flex-shrink: 0; }
.screenshot-stage { position: relative; aspect-ratio: var(--frame-ratio); }
.gallery-playback { position: absolute; right: 12px; bottom: 12px; display: grid; place-items: center; width: 44px; height: 44px; border-radius: 50%; color: white; background: #101418b3; backdrop-filter: blur(12px); }
.gallery-controls { text-align: center; width: 100%; }
.gallery-tabs { position: relative; display: inline-flex; flex-wrap: nowrap; gap: 0; padding: 4px; border-radius: 12px; background: var(--vp-c-bg-soft); border: 1px solid var(--vp-c-divider); }
.gallery-tabs button { position: relative; z-index: 1; flex: 1 1 0; min-height: 44px; padding: 8px 18px; border: 0; border-radius: 8px; background: transparent; white-space: nowrap; color: var(--vp-c-text-2); }
.gallery-tabs button[aria-pressed=true] { color: var(--vp-c-bg); background: transparent; }
.layout-ratio { margin-left: 5px; opacity: .6; font-size: 12px; }
.gallery-selection { position: absolute; top: 4px; bottom: 4px; left: 4px; width: calc((100% - 8px) / 3); border-radius: 8px; background: var(--vp-c-text-1); transform: translateX(calc(var(--selected) * 100%)); }
.screenshot-source { margin: 14px 0 0; }
.is-enhanced::after { content: ''; display: block; height: 45svh; }
.is-enhanced .screenshot-pin { position: sticky; top: calc(var(--vp-nav-height) + 24px); }
.is-enhanced .hero-product { transform: scale(var(--shot-scale)); transform-origin: top center; }
.is-enhanced .gallery-controls { opacity: var(--controls); }
@media (prefers-reduced-motion: no-preference) {
  .product-wrap { transition: width 400ms var(--ease-out); }
  .screenshot-stage { transition: aspect-ratio 400ms var(--ease-out); }
  .gallery-selection { transition: transform 400ms var(--ease-out); }
}
@media (max-width: 700px) {
  .screenshot-pin { gap: 20px; }
  .gallery-tabs button { padding: 8px 10px; font-size: 12px; }
  .layout-ratio { display: none; }
  .screenshot-source { font-size: 11px; }
}
</style>
