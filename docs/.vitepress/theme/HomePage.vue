<script setup>
import { withBase } from 'vitepress'
import { ref } from 'vue'
const active = ref(0)
const shots = [
  { file: 'landscape.webp', label: '横屏 · 大带小', caption: '主画面与多路小画面，在一面直播墙里观看。' },
  { file: 'danmaku.webp', label: '横屏 · 弹幕布局', caption: '关注列表、主画面与独立弹幕区，集中在同一个窗口。' },
  { file: 'portrait.webp', label: '竖屏布局', caption: '顶部关注栏与纵向画面墙，适合竖屏显示器。' }
]
</script>

<template>
  <main class="showcase">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">WINDOWS 10 / 11 · 开源直播观看工具</p>
        <h1>喜欢的直播，<br /><span>放在同一屏。</span></h1>
        <p class="hero-description">DD 监控室 CE 将多个直播间集中到一面画面墙。切换横竖屏布局，独立控制每一路声音，让观看跟着你的习惯走。</p>
        <div class="hero-actions"><a class="action primary" :href="withBase('/download')">下载软件</a>
          <a class="action secondary" :href="withBase('/plugins/')">浏览插件</a></div>
        <p class="hero-note">B 站由软件本体支持 · 更多平台通过独立插件扩展</p>
      </div>
      <div class="hero-product"><div class="window-label"><img :src="withBase('/logo.svg')" width="24" height="24" alt="" /><span>DD 监控室 CE</span><span class="window-mode">多画面观看</span></div>
        <img class="hero-shot" :src="withBase('/screenshots/landscape.webp')" width="1920" height="1080" alt="DD 监控室 CE 横屏直播墙，包含一个主画面和多个小画面" fetchpriority="high" />
        <div class="product-caption">真实软件截图 <span>横屏 · 大带小布局</span></div>
      </div>
    </section>

    <div class="facts"><div><strong>16 路</strong><span>最多同时管理</span></div><div><strong>横屏 / 竖屏</strong><span>按观看方式切换布局</span></div><div><strong>逐路控制</strong><span>音量、静音与画质</span></div><div><strong>独立插件</strong><span>按平台选择扩展</span></div></div>

    <section class="home-section">
      <div class="section-heading"><div><p class="eyebrow">观看与管理</p><h2>少切窗口，多看直播。</h2></div><a :href="withBase('/software')">了解软件功能</a></div>
      <div class="feature-grid"><article><span class="feature-number">01</span><h3>你的直播墙</h3><p>从关注列表拖入直播间，选择平分或大带小布局。主画面、小画面和弹幕，各有位置。</p></article><article><span class="feature-number">02</span><h3>声音各自掌握</h3><p>每个格子独立设置音量、静音和画质。需要专心看一路时，留下这一路的声音。</p></article><article><span class="feature-number">03</span><h3>关注不必反复找</h3><p>管理关注、置顶与排序，刷新开播状态。鼠标停留在已开播卡片上，可静音预览。</p></article></div>
    </section>

    <section class="home-section gallery-section">
      <div class="section-heading"><div><p class="eyebrow">真实界面</p><h2>选一套适合你的布局。</h2></div><span class="muted">现有项目文档截图</span></div>
      <div class="gallery-tabs" role="group" aria-label="截图布局"><button v-for="(shot, i) in shots" :key="shot.file" :aria-pressed="active === i" @click="active = i">{{ shot.label }}</button></div>
      <figure class="gallery"><a :href="withBase('/screenshots/' + shots[active].file)" target="_blank" rel="noopener" aria-label="在新标签页查看完整截图"><img :class="{ portrait: active === 2 }" :src="withBase('/screenshots/' + shots[active].file)" :alt="shots[active].caption" loading="lazy" /></a><figcaption>{{ shots[active].caption }} <span>点击查看完整截图</span></figcaption></figure>
    </section>

    <section class="home-section extension-section"><div><p class="eyebrow">插件扩展</p><h2>一面墙，更多平台。</h2><p>国内直播与海外直播分别启用。关注、拖入格子播放、画质选择、静音预览与聊天弹幕，沿用软件的观看方式。</p><a class="action primary" :href="withBase('/plugins/')">查看 1.0 插件</a><p class="hero-note">插件需配套更新的软件，安装前请查看兼容说明。</p></div><div class="extension-list"><a :href="withBase('/plugins/domestic_live')"><span class="extension-index">01</span><div><h3>国内直播平台</h3><p>虎牙 / 斗鱼 / 抖音</p></div><span class="version">v1.0</span></a><a :href="withBase('/plugins/global_live')"><span class="extension-index">02</span><div><h3>Twitch 与 YouTube</h3><p>Twitch / YouTube · 支持自动画质</p></div><span class="version">v1.0</span></a></div></section>

    <section class="home-bottom"><div><h2>从一个直播间开始。</h2><p>下载、解压、启动，再把关注拖进画面墙。</p></div><a class="action secondary" :href="withBase('/guide')">阅读安装帮助</a></section>
  </main>
</template>
