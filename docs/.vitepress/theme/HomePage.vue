<script setup>
import { withBase } from 'vitepress'
import { onMounted, onUnmounted, ref } from 'vue'
import plugins from '../../data/plugins.json'
import release from '../../data/software-release.json'

const active = ref(0)
const previewPlugin = ref(0)
const page = ref(null)
let observer
const shots = [
  { file: 'landscape.webp', label: '横屏观看', caption: '一个主画面，多路小画面。喜欢的直播，各有位置。' },
  { file: 'danmaku.webp', label: '弹幕布局', caption: '直播与聊天同屏，弹幕跟随主画面。' },
  { file: 'portrait.webp', label: '竖屏布局', caption: '关注移到顶部，画面纵向展开。' }
]

onMounted(() => {
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  if (motion.matches || !('IntersectionObserver' in window)) return
  observer = new IntersectionObserver(entries => {
    for (const { target, isIntersecting } of entries) {
      if (!isIntersecting) continue
      if (!motion.matches) target.animate([
        { opacity: 0.3, transform: 'translateY(24px)' },
        { opacity: 1, transform: 'translateY(0)' }
      ], { duration: 500, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' })
      observer.unobserve(target)
    }
  }, { threshold: 0.08 })
  page.value.querySelectorAll('[data-reveal]').forEach(element => observer.observe(element))
})
onUnmounted(() => observer?.disconnect())
</script>

<template>
  <main ref="page" class="showcase">
    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-copy">
        <p class="eyebrow">DD MONITOR CE <span>/</span> 为多路直播而生</p>
        <h1 id="hero-title">喜欢的直播，<br /><span>在同一屏相遇。</span></h1>
      </div>
      <div class="hero-intro">
        <a class="release-badge" :href="release.release">{{ release.tag }} 已发布 <span aria-hidden="true">↗</span></a>
        <p class="hero-description">把直播、声音和弹幕放进一面画面墙。横屏、竖屏、跨平台，按你的方式观看。</p>
        <div class="hero-actions"><a class="action primary" :href="release.download">下载 DD 监控室 CE <span aria-hidden="true">↗</span></a>
          <a class="action secondary" href="#plugins">探索插件</a></div>
        <p class="hero-note">Windows 10 / 11 · 免费开源 · B 站原生支持</p>
      </div>
      <div class="hero-stage">
        <div class="stage-caption"><span>你的专属直播现场</span><span>真实软件界面 / {{ shots[active].label }}</span></div>
        <div class="hero-product">
          <div class="window-label"><img :src="withBase('/logo.svg')" width="24" height="24" alt="" /><span>DD 监控室 CE</span><span class="window-mode">多画面 · 自在观看</span></div>
          <div class="screenshot-stage">
            <Transition name="screenshot">
              <img :key="shots[active].file" class="hero-shot" :class="{ portrait: active === 2 }"
                :src="withBase('/screenshots/' + shots[active].file)" :alt="shots[active].caption"
                width="1920" height="1080" fetchpriority="high" />
            </Transition>
          </div>
          <div class="product-caption"><span aria-live="polite">{{ shots[active].caption }}</span><a :href="withBase('/screenshots/' + shots[active].file)" target="_blank" rel="noopener">查看完整截图</a></div>
        </div>
        <div class="gallery-tabs" role="group" aria-label="截图布局"><button v-for="(shot, i) in shots" :key="shot.file" :aria-pressed="active === i" @click="active = i"><span class="tab-number">0{{ i + 1 }}</span>{{ shot.label }}</button></div>
        <p class="screenshot-source">截图来自现有项目文档，不同版本的界面细节可能有所调整。</p>
      </div>
    </section>

    <div class="facts" data-reveal><div><strong>16<span> 路</span></strong><span>最多同时管理直播</span></div><div><strong>横屏 / 竖屏</strong><span>一套适合你的布局</span></div><div><strong>独立控制</strong><span>音量、静音与画质</span></div><div><strong>{{ plugins.length }} 款插件</strong><span>扩展平台与观看方式</span></div></div>

    <section class="home-section viewing-section" data-reveal>
      <div class="section-heading"><div><p class="eyebrow">01 / 观看体验</p><h2>把直播墙，<br />调成你的样子。</h2></div><div><p class="section-description">0.4 新功能 · 外观、布局与观看组合</p><a :href="withBase('/software')">了解全部软件功能 ↗</a></div></div>
      <FeatureDemo />
    </section>

    <section id="plugins" class="home-section home-plugins" data-reveal aria-labelledby="plugins-title">
      <div class="section-heading"><div><p class="eyebrow">02 / 插件扩展</p><h2 id="plugins-title">让直播墙，多一点可能。</h2></div><a :href="withBase('/plugins/')">全部插件与安装说明 ↗</a></div>
      <div class="home-plugin-grid"><article v-for="(plugin, i) in plugins" :key="plugin.id" class="home-plugin-card">
        <div class="card-top"><span class="plugin-category">0{{ i + 1 }} / {{ plugin.category }}</span><span class="version">v{{ plugin.version }}</span></div>
        <div class="platform-icons"><img v-for="(icon, j) in plugin.icons" :key="icon" :src="withBase('/platforms/' + icon)" :alt="plugin.platforms[j]" width="32" height="32" loading="lazy" /></div>
        <h3><a :href="withBase('/plugins/' + plugin.id)">{{ plugin.name }}</a></h3><p>{{ plugin.description }}</p>
        <div class="platform-tags"><span v-for="platform in plugin.platforms" :key="platform">{{ platform }}</span></div>
        <ul><li v-for="feature in plugin.features" :key="feature">{{ feature }}</li></ul>
        <button class="plugin-demo-trigger" :aria-pressed="previewPlugin === i" aria-controls="plugin-demo" @click="previewPlugin = i">查看功能流程 <span aria-hidden="true">↗</span></button>
        <div class="card-actions"><a class="action secondary" :href="plugin.download">下载 v{{ plugin.version }} ZIP</a><a class="detail-link" :href="withBase('/plugins/' + plugin.id)">查看详情 ↗</a></div>
      </article></div>
      <div id="plugin-demo" class="plugin-demo-panel" aria-live="polite">
        <div class="plugin-demo-heading"><p class="eyebrow">使用流程</p><h3>{{ plugins[previewPlugin].name }}</h3><a :href="withBase('/plugins/' + plugins[previewPlugin].id)">阅读完整说明 ↗</a></div>
        <ol class="plugin-flow"><li v-for="(step, i) in plugins[previewPlugin].steps" :key="step"><span>0{{ i + 1 }}</span><p>{{ step }}</p></li></ol>
      </div>
      <p class="plugin-compatibility">插件独立下载、独立启用。安装前请查看<a :href="withBase('/download#插件兼容性')">软件版本与兼容要求</a>。</p>
    </section>

    <section class="home-bottom" data-reveal><div><p class="eyebrow">准备好你的下一场直播</p><h2>打开软件，<br />把喜欢的直播拖进来。</h2><p>下载 Windows 便携版，完整解压后即可开始。</p></div><div class="bottom-actions"><a class="action primary" :href="withBase('/download')">获取软件 <span aria-hidden="true">↗</span></a><a :href="withBase('/guide')">第一次使用？阅读安装帮助</a></div></section>
  </main>
</template>
