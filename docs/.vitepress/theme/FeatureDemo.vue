<script setup>
import { withBase } from 'vitepress'
import { ref } from 'vue'

const active = ref(0)
const features = [
  { file: 'landscape.webp', name: '横屏布局', title: '主次分明，多路同看', description: '一个主画面搭配多路小画面，在同一面直播墙里安排喜欢的直播。' },
  { file: 'danmaku.webp', name: '弹幕同屏', title: '画面与聊天，一起关注', description: '直播画面与弹幕区域并排显示，观看直播时也能留意聊天内容。' },
  { file: 'portrait.webp', name: '竖屏观看', title: '沿着屏幕，纵向展开', description: '关注列表移到顶部，主画面和多路直播沿竖屏排列。' }
]
</script>

<template>
  <div class="feature-gallery">
    <div class="feature-thumbnails" role="group" aria-label="观看体验截图">
      <button v-for="(feature, i) in features" :key="feature.file" class="feature-thumbnail" :aria-pressed="active === i"
        aria-controls="feature-screenshot" @click="active = i">
        <span class="thumbnail-image"><img :src="withBase('/screenshots/' + feature.file)" alt="" width="160" height="100" loading="lazy" /></span>
        <span class="thumbnail-copy"><span class="thumbnail-number">0{{ i + 1 }}</span><strong>{{ feature.name }}</strong><span>{{ feature.title }}</span></span>
      </button>
    </div>
    <figure id="feature-screenshot" class="feature-screenshot">
      <div class="feature-image">
        <Transition name="screenshot">
          <img :key="features[active].file" :src="withBase('/screenshots/' + features[active].file)"
            :alt="'DD 监控室 CE ' + features[active].name + '真实软件截图'" width="1920" height="1080" loading="lazy" />
        </Transition>
      </div>
      <figcaption aria-live="polite"><span class="eyebrow">真实软件截图 / {{ features[active].name }}</span><h3>{{ features[active].title }}</h3><p>{{ features[active].description }}</p></figcaption>
    </figure>
  </div>
</template>

<style scoped>
.feature-gallery { display: grid; grid-template-columns: minmax(260px, .7fr) minmax(0, 1.3fr); gap: 36px; align-items: center; }
.feature-thumbnails { display: grid; gap: 12px; }
.feature-thumbnail { display: flex; gap: 16px; align-items: center; padding: 14px; text-align: left; border: 1px solid var(--vp-c-divider); border-radius: 12px; background: var(--vp-c-bg-alt); color: var(--vp-c-text-2); }
.feature-thumbnail[aria-pressed=true] { border-color: var(--vp-c-brand-1); color: var(--vp-c-text-1); background: var(--vp-c-bg-soft); }
.thumbnail-image { display: grid; place-items: center; flex: 0 0 104px; height: 68px; overflow: hidden; border-radius: 6px; background: #090c10; }
.thumbnail-image img { width: 100%; height: 100%; object-fit: contain; }
.thumbnail-copy { display: grid; gap: 5px; }
.thumbnail-number { color: var(--vp-c-brand-1); font-size: 11px; }
.thumbnail-copy strong { font-size: 16px; font-weight: 600; }
.thumbnail-copy > span:last-child { font-size: 12px; line-height: 1.6; }
.feature-screenshot { margin: 0; padding: 18px; min-width: 0; border: 1px solid var(--vp-c-divider); border-radius: 14px; background: var(--vp-c-bg-alt); }
.feature-image { display: grid; place-items: center; height: clamp(240px, 32vw, 440px); }
.feature-image img { grid-area: 1 / 1; display: block; width: auto; height: auto; max-width: 100%; max-height: 100%; min-height: 0; border-radius: 6px; }
figcaption { margin-top: 20px; }
figcaption .eyebrow { display: block; margin: 0 0 10px; font-size: 11px; }
figcaption h3 { margin: 0 0 8px; font-size: 20px; font-weight: 600; line-height: 1.5; }
figcaption p { margin: 0; color: var(--vp-c-text-2); font-size: 14px; line-height: 1.8; }
@media (max-width: 900px) {
  .feature-gallery { grid-template-columns: 1fr; gap: 20px; }
  .feature-thumbnails { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .feature-thumbnail { flex-direction: column; gap: 10px; padding: 10px; align-items: stretch; }
  .thumbnail-image { flex-basis: auto; width: 100%; height: 76px; }
  .thumbnail-copy > span { display: none; }
  .thumbnail-copy strong { text-align: center; font-size: 13px; }
}
@media (max-width: 480px) {
  .feature-screenshot { padding: 12px; }
  .thumbnail-image { height: 52px; }
  .feature-thumbnail { padding: 7px; }
}
</style>
