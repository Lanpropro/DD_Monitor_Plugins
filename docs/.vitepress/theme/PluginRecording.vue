<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { withBase } from 'vitepress'

const props = defineProps({ plugin: { type: Object, required: true } })
const recordings = {
  domestic_live: { file:'domestic-live', caption:'国内平台 1＋5 多画面实录 · 无声', note:'来自宣传片2的连续软件录屏，保留原始播放速度。' },
  global_live: { file:'global-live', caption:'海外平台九格展示 · 无声', note:'沿用宣传片2的九格剪辑；各格取自不同时间段，不代表同一时刻九路同时播放的性能。' }
}
const recording = computed(() => recordings[props.plugin.id])
const root = ref(null)
const video = ref(null)
let observer
function pause() { video.value?.pause() }
function onVisibility() { if (document.hidden) pause() }
onMounted(() => {
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(entries => { if (!entries[0].isIntersecting) pause() }, { threshold:.2 })
    observer.observe(root.value)
  }
  document.addEventListener('visibilitychange', onVisibility)
})
onUnmounted(() => { pause(); observer?.disconnect(); document.removeEventListener('visibilitychange', onVisibility) })
</script>

<template>
  <figure ref="root" class="plugin-recording" :aria-label="plugin.name + '视频展示'">
    <video ref="video" :src="withBase('/videos/' + recording.file + '-recording.mp4')" :poster="withBase('/videos/' + recording.file + '-poster.webp')" :aria-label="plugin.name + '展示视频，无声'" width="1920" height="1080" controls muted playsinline preload="none"></video>
    <figcaption><span>{{ recording.caption }}</span><p>{{ recording.note }}</p></figcaption>
  </figure>
</template>

<style scoped>
.plugin-recording { margin:0; min-width:0; }
.plugin-recording video { display:block; width:100%; height:auto; aspect-ratio:16/9; object-fit:contain; border:1px solid var(--vp-c-divider); border-radius:12px; background:#101114; }
.plugin-recording figcaption { margin-top:12px; color:var(--vp-c-text-2); font-size:12px; line-height:1.8; }
.plugin-recording figcaption p { margin:4px 0 0; }
</style>
