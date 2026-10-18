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
const enhanced = ref(false)
const progress = ref(1)
const paused = ref(false)
let motion, observer, timer, frame = 0, visible = false, interacting = false

function stop() { clearTimeout(timer); timer = undefined }
function schedule() {
  stop()
  if (!visible || paused.value || interacting || motion?.matches || document.hidden || progress.value < .65) return
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
  schedule()
}
function onScroll() { if (!frame) frame = requestAnimationFrame(update) }
function preferenceChanged() {
  enhanced.value = !motion.matches && !!observer
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
  motion?.removeEventListener('change', preferenceChanged)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
  document.removeEventListener('visibilitychange', schedule)
})
</script>

<template>
  <section ref="story" class="screenshot-story" :class="{ 'is-enhanced': enhanced }" aria-label="真实软件截图"
    :style="{ '--shot-scale': 1 - progress * .18, '--controls': Math.min(1, Math.max(0, (progress - .55) / .4)) }">
    <div ref="pin" class="screenshot-pin">
      <div class="hero-product">
        <div class="window-label"><img :src="withBase('/logo.svg')" width="24" height="24" alt="" /><span>DD 监控室 CE</span><span class="window-mode">多画面 · 自在观看</span></div>
        <div class="screenshot-stage">
          <Transition name="screenshot">
            <img :key="shots[active].file" class="hero-shot" :src="withBase('/screenshots/' + shots[active].file)"
              :alt="shots[active].caption" width="1920" height="1080" fetchpriority="high" />
          </Transition>
        </div>
        <div class="product-caption">{{ shots[active].caption }}</div>
      </div>
      <div class="gallery-controls" :inert="enhanced && progress < .65" @pointerenter="interact(true)" @pointerleave="interact(false)"
        @focusin="interact(true)" @focusout="interact(false)">
        <p class="eyebrow">真实软件界面 / 三种观看布局</p>
        <div class="gallery-tabs" role="group" aria-label="截图布局">
          <button v-for="(shot, i) in shots" :key="shot.file" :aria-pressed="active === i" @click="selectShot(i)"><span class="tab-number">0{{ i + 1 }}</span>{{ shot.label }}</button>
          <button class="gallery-playback" :aria-label="paused ? '播放截图轮播' : '暂停截图轮播'" :aria-pressed="paused" @click="togglePlayback">{{ paused ? '播放' : '暂停' }}</button>
        </div>
        <p class="screenshot-source">截图来自现有项目文档，不同版本的界面细节可能有所调整。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.screenshot-story { grid-column: 1 / -1; min-width: 0; }
.screenshot-pin { display: flex; flex-direction: column; align-items: center; gap: 28px; }
.hero-product { width: 100%; flex-shrink: 0; }
.gallery-controls { text-align: center; width: 100%; }
.gallery-controls .eyebrow { margin: 0; font-size: 12px; }
.gallery-tabs { padding: 14px 0; }
.gallery-tabs button { border-radius: 24px; min-height: 44px; }
.gallery-tabs .gallery-playback { color: var(--vp-c-text-2); font-size: 12px; }
.screenshot-source { margin: 0; }
.is-enhanced { height: calc(160svh - var(--vp-nav-height) - 48px); }
.is-enhanced .screenshot-pin { position: sticky; top: calc(var(--vp-nav-height) + 24px); height: calc(100svh - var(--vp-nav-height) - 48px); }
.is-enhanced .hero-product { width: min(100%, calc((100svh - var(--vp-nav-height) - 180px) * 1.7778)); transform: scale(var(--shot-scale)); transform-origin: top center; }
.is-enhanced .gallery-controls { position: absolute; bottom: 0; opacity: var(--controls); transform: translateY(calc((1 - var(--controls)) * 18px)); }
@media (max-width: 700px) {
  .is-enhanced { height: calc(76vw + 100px + 35svh); }
  .is-enhanced .screenshot-pin { height: calc(76vw + 100px); }
  .is-enhanced .hero-product { width: 100%; }
  .gallery-tabs { gap: 6px; flex-wrap: wrap; }
  .gallery-tabs button { padding: 8px 8px; font-size: 12px; flex: 0 0 auto; white-space: nowrap; }
  .gallery-tabs .gallery-playback { padding-inline: 10px; }
  .screenshot-source { font-size: 11px; }
}
</style>
