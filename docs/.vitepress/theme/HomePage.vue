<script setup>
import { withBase } from 'vitepress'
import { onMounted, onUnmounted, ref } from 'vue'
import plugins from '../../data/plugins.json'
import release from '../../data/software-release.json'
import SplashWall from './SplashWall.vue'
import ScreenshotShowcase from './ScreenshotShowcase.vue'
import PluginCarousel from './PluginCarousel.vue'
import PluginFeatureMotion from './PluginFeatureMotion.vue'
import BackToIntro from './BackToIntro.vue'
import MatchSyncTimeline from './MatchSyncTimeline.vue'

const previewPlugin = ref(2)
const page = ref(null)
let observer
let motion
const pending = new Set()
const running = new Set()
function showRemaining() {
  if (!motion.matches) return
  for (const target of pending) target.classList.remove('reveal-pending')
  pending.clear()
  for (const animation of running) animation.cancel()
  running.clear()
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
      if (!motion.matches) {
        const animation = target.animate([
          { opacity: 0, transform: 'translateY(24px)' },
          { opacity: 1, transform: 'translateY(0)' }
        ], { duration: 650, delay: Number(target.dataset.revealDelay || 0), fill: 'backwards', easing: 'cubic-bezier(0.23, 1, 0.32, 1)' })
        running.add(animation)
        animation.onfinish = () => running.delete(animation)
      }
      observer.unobserve(target)
    }
  }, { threshold: 0, rootMargin: '0px 0px -80px 0px' })
  page.value.querySelectorAll('[data-reveal]').forEach(element => {
    element.classList.add('reveal-pending')
    pending.add(element)
    observer.observe(element)
  })
  motion.addEventListener('change', showRemaining)
})
onUnmounted(() => {
  observer?.disconnect()
  for (const animation of running) animation.cancel()
  running.clear()
  motion?.removeEventListener('change', showRemaining)
})
</script>

<template>
  <SplashWall />
  <main id="home-intro" ref="page" class="showcase">
    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-copy" data-reveal>
        <p class="eyebrow">DD_Monitor_CE <span>/</span> 为多路直播而生</p>
        <h1 id="hero-title" tabindex="-1">喜欢的直播，<br /><span>在同一屏相遇。</span></h1>
      </div>
      <div class="hero-intro" data-reveal data-reveal-delay="120">
        <a class="release-badge" :href="release.release">{{ release.tag }} 已发布 <span aria-hidden="true">↗</span></a>
        <p class="hero-description">把直播、声音和弹幕放进一面画面墙。横屏、竖屏，按你的方式观看。</p>
        <div class="hero-actions"><a class="action primary" :href="release.download">下载 DD 监控室 CE <span aria-hidden="true">↗</span></a>
          <a class="action secondary" href="#plugins">探索插件</a></div>
        <p class="hero-note">Windows 10 / 11 · 免费开源 · B 站原生支持</p>
      </div>
      <ScreenshotShowcase>
        <div class="facts"><div><strong>16<span> 路</span></strong><span>最多同时管理直播</span></div><div><strong>横屏 / 竖屏</strong><span>一套适合你的布局</span></div><div><strong>独立控制</strong><span>音量、静音与画质</span></div><div><strong>{{ plugins.length }} 款插件</strong><span>扩展平台与观看方式</span></div></div>
      </ScreenshotShowcase>
    </section>

    <section class="home-section viewing-section">
      <div class="section-heading"><div data-reveal><p class="eyebrow">01 / 观看体验</p><h2>把直播墙，<br />调成你的样子。</h2></div><div data-reveal data-reveal-delay="120"><p class="section-description">切换布局预设 · 拖入关注卡片</p><a :href="withBase('/software')">了解全部软件功能 ↗</a></div></div>
      <FeatureDemo />
    </section>

    <section id="plugins" class="home-section home-plugins" aria-labelledby="plugins-title">
      <div class="section-heading"><div data-reveal><p class="eyebrow">02 / 插件扩展</p><h2 id="plugins-title">让直播墙，多一点可能。</h2></div><a data-reveal data-reveal-delay="120" :href="withBase('/plugins/')">全部插件与安装说明 ↗</a></div>
      <PluginCarousel :initial-index="2" v-slot="{ duplicate }" data-reveal><article v-for="(plugin, i) in plugins" :key="plugin.id" class="home-plugin-card" :class="{ 'is-selected': previewPlugin === i }">
        <div class="card-top"><div class="platform-icons"><img v-for="(icon, j) in plugin.icons" :key="icon" :src="withBase('/platforms/' + icon)" :alt="plugin.platforms[j]" width="32" height="32" loading="lazy" /></div><span class="version">v{{ plugin.version }}</span></div>
        <span class="plugin-category">0{{ i + 1 }} / {{ plugin.category }}</span>
        <h3><a :tabindex="duplicate ? -1 : undefined" :href="withBase('/plugins/' + plugin.id)">{{ plugin.name }}</a></h3><p>{{ plugin.description }}</p>
        <div class="platform-tags"><span v-for="platform in plugin.platforms" :key="platform">{{ platform }}</span></div>
        <ul><li v-for="feature in plugin.features" :key="feature">{{ feature }}</li></ul>
        <button :tabindex="duplicate ? -1 : undefined" class="plugin-demo-trigger" :aria-pressed="previewPlugin === i" aria-controls="plugin-demo" :aria-label="'查看' + plugin.name + '的具体功能'" @click="previewPlugin = i"></button>
        <div class="card-actions"><a :tabindex="duplicate ? -1 : undefined" class="action primary" :href="plugin.download">下载 ZIP</a><a :tabindex="duplicate ? -1 : undefined" class="detail-link" :href="withBase('/plugins/' + plugin.id)">查看详情 ↗</a></div>
      </article></PluginCarousel>
      <div id="plugin-demo" class="plugin-demo-panel" :class="{ 'is-match-demo': plugins[previewPlugin].id === 'match_sync' }">
        <div class="plugin-demo-heading" data-reveal aria-live="polite"><p class="eyebrow">插件功能</p><h3>{{ plugins[previewPlugin].name }}</h3><p class="plugin-detail-description">{{ plugins[previewPlugin].description }}</p><a :href="withBase('/plugins/' + plugins[previewPlugin].id)">阅读完整说明 ↗</a></div>
        <div class="plugin-demo-content"><MatchSyncTimeline v-if="plugins[previewPlugin].id === 'match_sync'" /><PluginFeatureMotion data-reveal v-else :key="plugins[previewPlugin].id" :plugin="plugins[previewPlugin]" /><template v-if="plugins[previewPlugin].id !== 'match_sync'"><p class="plugin-flow-title">使用流程</p><ol class="plugin-flow"><li v-for="(step, i) in plugins[previewPlugin].steps" :key="step"><span>0{{ i + 1 }}</span><p>{{ step }}</p></li></ol></template></div>
      </div>
      <p class="plugin-compatibility">插件独立下载、独立启用。安装前请查看<a :href="withBase('/download#插件兼容性')">软件版本与兼容要求</a>。</p>
    </section>

    <section class="home-bottom"><div data-reveal><p class="eyebrow">准备好你的下一场观看</p><h2>打开软件，<br />把喜欢的直播拖进来。</h2></div><div class="bottom-actions" data-reveal data-reveal-delay="120"><a class="action primary" :href="withBase('/download')">获取软件 <span aria-hidden="true">↗</span></a><a :href="withBase('/guide')">第一次使用？阅读安装帮助</a></div></section>
  </main>
  <BackToIntro />
</template>
