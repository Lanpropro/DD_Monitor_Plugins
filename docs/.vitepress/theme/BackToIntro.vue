<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const visible = ref(false)
let frame = 0
function introTop() {
  const anchor = document.getElementById('home-intro')
  if (!anchor) return null
  const nav = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--vp-nav-height')) || 64
  // Leave the introduction below the navigation, with the opening wall behind it.
  return Math.max(nav + 1, window.scrollY + anchor.getBoundingClientRect().top - Math.max(nav + 24, window.innerHeight * .28))
}
function update() {
  frame = 0
  const top = introTop()
  visible.value = top !== null && window.scrollY > top + window.innerHeight * .7
}
function onScroll() { if (!frame) frame = requestAnimationFrame(update) }
function returnToIntro() {
  const top = introTop()
  if (top === null) return
  document.getElementById('hero-title')?.focus({ preventScroll: true })
  window.scrollTo({ top, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
}
onMounted(() => { update(); window.addEventListener('scroll', onScroll, { passive: true }); window.addEventListener('resize', onScroll, { passive: true }) })
onUnmounted(() => { cancelAnimationFrame(frame); window.removeEventListener('scroll', onScroll); window.removeEventListener('resize', onScroll) })
</script>

<template>
  <button v-show="visible" class="back-to-intro" aria-label="返回首页介绍" title="返回首页介绍" @click="returnToIntro"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 11 6-6 6 6M12 5v14" /></svg></button>
</template>

<style scoped>
.back-to-intro { position:fixed; right:24px; bottom:max(24px,env(safe-area-inset-bottom)); z-index:50; display:grid; place-items:center; width:46px; height:46px; border:1px solid var(--vp-c-divider); border-radius:50%; color:var(--vp-c-text-1); background:var(--vp-c-bg-elv); box-shadow:0 6px 24px #0002; transition:background-color 160ms ease; }
@media (hover:hover) and (pointer:fine) { .back-to-intro:hover { background:var(--button-surface-hover); } }
@media (max-width:700px) { .back-to-intro { right:16px; bottom:max(16px,env(safe-area-inset-bottom)); } }
@media print { .back-to-intro { display:none; } }
</style>
