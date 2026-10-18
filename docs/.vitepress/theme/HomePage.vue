<script setup>
import { withBase } from 'vitepress'
import { onMounted, onUnmounted, ref } from 'vue'
import plugins from '../../data/plugins.json'
import release from '../../data/software-release.json'
import SplashWall from './SplashWall.vue'
import ScreenshotShowcase from './ScreenshotShowcase.vue'

const previewPlugin = ref(0)
const page = ref(null)
let observer
let motion
const pending = new Set()
function showRemaining() {
  if (!motion.matches) return
  for (const target of pending) target.classList.remove('reveal-pending')
  pending.clear()
  observer?.disconnect()
}

onMounted(() => {
  motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  if (motion.matches || !('IntersectionObserver' in window)) return
  observer = new IntersectionObserver(entries => {
    for (const { target, isIntersecting } of entries) {
      if (!isIntersecting) continue
      target.classList.remove('reveal-pending')
      pending.delete(target)
      if (!motion.matches) target.animate([
        { opacity: 0, transform: 'translateY(24px)' },
        { opacity: 1, transform: 'translateY(0)' }
      ], { duration: 500, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' })
      observer.unobserve(target)
    }
  }, { threshold: 0.08, rootMargin: '0px 0px -48px 0px' })
  page.value.querySelectorAll('[data-reveal]').forEach(element => {
    element.classList.add('reveal-pending')
    pending.add(element)
    observer.observe(element)
  })
  motion.addEventListener('change', showRemaining)
})
onUnmounted(() => { observer?.disconnect(); motion?.removeEventListener('change', showRemaining) })
</script>

<template>
  <SplashWall />
  <main ref="page" class="showcase">
    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-copy" data-reveal>
        <p class="eyebrow">DD MONITOR CE <span>/</span> 为多路直播而生</p>
        <h1 id="hero-title">喜欢的直播，<br /><span>在同一屏相遇。</span></h1>
      </div>
      <div class="hero-intro" data-reveal>
        <a class="release-badge" :href="release.release">{{ release.tag }} 已发布 <span aria-hidden="true">↗</span></a>
        <p class="hero-description">把直播、声音和弹幕放进一面画面墙。横屏、竖屏、跨平台，按你的方式观看。</p>
        <div class="hero-actions"><a class="action primary" :href="release.download">下载 DD 监控室 CE <span aria-hidden="true">↗</span></a>
          <a class="action secondary" href="#plugins">探索插件</a></div>
        <p class="hero-note">Windows 10 / 11 · 免费开源 · B 站原生支持</p>
      </div>
      <ScreenshotShowcase />
    </section>

    <div class="facts" data-reveal><div><strong>16<span> 路</span></strong><span>最多同时管理直播</span></div><div><strong>横屏 / 竖屏</strong><span>一套适合你的布局</span></div><div><strong>独立控制</strong><span>音量、静音与画质</span></div><div><strong>{{ plugins.length }} 款插件</strong><span>扩展平台与观看方式</span></div></div>

    <section class="home-section viewing-section">
      <div class="section-heading" data-reveal><div><p class="eyebrow">01 / 观看体验</p><h2>把直播墙，<br />调成你的样子。</h2></div><div><p class="section-description">布局比例、深浅外观与观看组合</p><a :href="withBase('/software')">了解全部软件功能 ↗</a></div></div>
      <div data-reveal><FeatureDemo /></div>
    </section>

    <section id="plugins" class="home-section home-plugins" aria-labelledby="plugins-title">
      <div class="section-heading" data-reveal><div><p class="eyebrow">02 / 插件扩展</p><h2 id="plugins-title">让直播墙，多一点可能。</h2></div><a :href="withBase('/plugins/')">全部插件与安装说明 ↗</a></div>
      <div class="home-plugin-grid"><article v-for="(plugin, i) in plugins" :key="plugin.id" class="home-plugin-card" data-reveal>
        <div class="card-top"><div class="platform-icons"><img v-for="(icon, j) in plugin.icons" :key="icon" :src="withBase('/platforms/' + icon)" :alt="plugin.platforms[j]" width="32" height="32" loading="lazy" /></div><span class="version">v{{ plugin.version }}</span></div>
        <span class="plugin-category">0{{ i + 1 }} / {{ plugin.category }}</span>
        <h3><a :href="withBase('/plugins/' + plugin.id)">{{ plugin.name }}</a></h3><p>{{ plugin.description }}</p>
        <div class="platform-tags"><span v-for="platform in plugin.platforms" :key="platform">{{ platform }}</span></div>
        <ul><li v-for="feature in plugin.features" :key="feature">{{ feature }}</li></ul>
        <button class="plugin-demo-trigger" :aria-pressed="previewPlugin === i" aria-controls="plugin-demo" @click="previewPlugin = i">查看功能流程 <span aria-hidden="true">↗</span></button>
        <div class="card-actions"><a class="action primary" :href="plugin.download">下载 ZIP</a><a class="detail-link" :href="withBase('/plugins/' + plugin.id)">查看详情 ↗</a></div>
      </article></div>
      <div id="plugin-demo" class="plugin-demo-panel" data-reveal aria-live="polite">
        <div class="plugin-demo-heading"><p class="eyebrow">使用流程</p><h3>{{ plugins[previewPlugin].name }}</h3><a :href="withBase('/plugins/' + plugins[previewPlugin].id)">阅读完整说明 ↗</a></div>
        <ol class="plugin-flow"><li v-for="(step, i) in plugins[previewPlugin].steps" :key="step"><span>0{{ i + 1 }}</span><p>{{ step }}</p></li></ol>
      </div>
      <p class="plugin-compatibility">插件独立下载、独立启用。安装前请查看<a :href="withBase('/download#插件兼容性')">软件版本与兼容要求</a>。</p>
    </section>

    <section class="home-bottom" data-reveal><div><p class="eyebrow">准备好你的下一场直播</p><h2>打开软件，<br />把喜欢的直播拖进来。</h2><p>下载 Windows 便携版，完整解压后即可开始。</p></div><div class="bottom-actions"><a class="action primary" :href="withBase('/download')">获取软件 <span aria-hidden="true">↗</span></a><a :href="withBase('/guide')">第一次使用？阅读安装帮助</a></div></section>
  </main>
</template>
