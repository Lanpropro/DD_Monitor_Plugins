<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

// 16 格对应软件最多同时管理的 16 路，整面墙只是氛围，不含真实画面。
const tiles = 16
const section = ref(null)
const wall = ref(null)
let frame = 0
let pending = false
let reduced = false

// 只写 transform / opacity，滚动时停在合成层，不触发布局。
function apply() {
  pending = false
  const element = wall.value
  if (!element) return
  const viewport = window.innerHeight || 1
  const progress = reduced ? 0 : Math.min(1, Math.max(0, (window.scrollY || 0) / (viewport * 0.85)))
  element.style.transform = `translate3d(0, ${(progress * 26).toFixed(2)}vh, 0) scale(${(1 - progress * 0.14).toFixed(3)})`
  element.style.opacity = (1 - progress * 0.72).toFixed(3)
  section.value?.style.setProperty('--splash-progress', progress.toFixed(3))
}

function onScroll() {
  if (pending) return
  pending = true
  frame = requestAnimationFrame(apply)
}

onMounted(() => {
  reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  apply()
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll, { passive: true })
})

onUnmounted(() => {
  cancelAnimationFrame(frame)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
})
</script>

<template>
  <section ref="section" class="splash" aria-hidden="true">
    <div ref="wall" class="splash-wall">
      <span v-for="n in tiles" :key="n" class="splash-tile" :style="{ '--i': n - 1 }"></span>
    </div>
    <p class="splash-hint">向下滚动<span class="splash-arrow">↓</span></p>
  </section>
</template>

<style scoped>
.splash {
  position: sticky;
  top: var(--vp-nav-height);
  z-index: 0;
  height: calc(100vh - var(--vp-nav-height));
  height: calc(100dvh - var(--vp-nav-height));
  display: grid;
  place-items: center;
  overflow: hidden;
  background: radial-gradient(120% 85% at 50% 0%, color-mix(in srgb, var(--vp-c-brand-1) 15%, transparent), transparent 68%);
}
.splash-wall {
  width: min(1060px, 86vw);
  aspect-ratio: 16 / 9;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  grid-template-rows: repeat(4, 1fr);
  gap: clamp(6px, 1vw, 14px);
  margin-bottom: 9vh;
  transform-origin: 50% 100%;
  will-change: transform, opacity;
}
.splash-tile {
  border-radius: 10px;
  border: 1px solid var(--vp-c-divider);
  background:
    linear-gradient(150deg, color-mix(in srgb, var(--vp-c-brand-1) 16%, transparent), transparent 62%),
    var(--vp-c-bg-alt);
  box-shadow: inset 0 1px 0 #ffffff10;
  animation: tile-rise 760ms var(--ease-out) both;
  animation-delay: calc(var(--i) * 34ms);
}
/* 每格错峰浮现，像画面一路一路接进来。 */
@keyframes tile-rise {
  from { opacity: 0; transform: translateY(26px) scale(.94); }
  to { opacity: 1; transform: none; }
}
.splash-hint {
  position: absolute;
  bottom: 5.5vh;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--vp-c-text-2);
  font-size: 13px;
  letter-spacing: .1em;
  animation: hint-in 600ms 780ms var(--ease-out) both;
}
.splash-arrow { animation: hint-nudge 1.8s 1.4s ease-in-out infinite; }
@keyframes hint-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes hint-nudge {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(4px); }
}
@media (prefers-reduced-motion: reduce) {
  .splash-tile { animation: none; }
  .splash-hint { animation: none; }
  .splash-arrow { animation: none; }
}
</style>
