<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { withBase } from 'vitepress'

// Recorded source: 二路直播对齐.mp4, 00:06–00:34 (宣传片2的连续二路素材).
const chapters = [
  { label:'对照画面', start:0, end:6, description:'真实录屏：主画面下方保留两路计时区域，方便对照同一场比赛。' },
  { label:'偏移微调', start:6, end:16, description:'本段展示手动偏移微调，可作为自动对齐后的补充；不是自动对齐成功过程的实录。' },
  { label:'同步观看', start:16, end:23, description:'继续观看微调后的比赛画面与两路对照区域。视频默认静音，这份录屏没有直播原声。' },
  { label:'合并弹幕', start:23, end:28, description:'收起下方对照区，继续观看比赛与带来源标记的弹幕。' }
]
const duration = 28
const root = ref(null)
const video = ref(null)
const time = ref(0)
const paused = ref(false)
const reduced = ref(false)
const playing = ref(false)
const active = computed(() => {
  const index = chapters.findIndex(chapter => time.value < chapter.end)
  return index < 0 ? chapters.length - 1 : index
})
const clock = value => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2,'0')}`
let media, observer, visible = false, held = false, focused = false
function sync() {
  playing.value = visible && !paused.value && !reduced.value && !held && !focused && !document.hidden
  if (playing.value) video.value?.play()?.catch(() => { if (playing.value) { paused.value = true; playing.value = false } })
  else video.value?.pause()
}
function seek(value) {
  time.value = Math.max(0, Math.min(duration, Number(value)))
  if (video.value?.readyState) video.value.currentTime = time.value
  else video.value?.load()
  sync()
}
function onLoaded() { video.value.currentTime = time.value; sync() }
function onTimeUpdate() { time.value = Math.min(duration, video.value.currentTime) }
function onEnded() { time.value = 0; video.value.currentTime = 0; sync() }
function jump(index) { if (chapters[index]) seek(chapters[index].start) }
function togglePlayback() {
  if (reduced.value) return
  paused.value = !paused.value
  if (!paused.value && time.value === duration) seek(0)
  sync()
}
function hold(value) { held = value; sync() }
function onFocus(event) { focused = event.target.matches('.timeline-seek:focus-visible'); sync() }
function onBlur(event) { if (!event.currentTarget.contains(event.relatedTarget)) { focused = false; sync() } }
function preferenceChanged() { reduced.value = media.matches; sync() }
onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; sync() }, { threshold:.2 })
    observer.observe(root.value)
  } else { visible = true; paused.value = true }
  preferenceChanged()
  media.addEventListener('change', preferenceChanged)
  document.addEventListener('visibilitychange', sync)
})
onUnmounted(() => { video.value?.pause(); observer?.disconnect(); media?.removeEventListener('change', preferenceChanged); document.removeEventListener('visibilitychange', sync) })
</script>

<template>
  <div ref="root" class="match-sync-demo" role="group" aria-label="比赛二路同步实录预览" aria-live="off" @focusin="onFocus" @focusout="onBlur">
    <div class="sync-screen" data-reveal>
      <video ref="video" class="sync-software" :src="withBase('/videos/match-sync-recording.mp4')" :poster="withBase('/videos/match-sync-poster.webp')" aria-label="比赛二路手动微调与弹幕合并实录，无声" width="1920" height="1080" muted playsinline preload="none" @loadedmetadata="onLoaded" @timeupdate="onTimeUpdate" @ended="onEnded"></video>
    </div>
    <div class="recording-controls"><span>真实软件录屏 · 手动微调 · 静音播放</span><button class="screen-playback" :disabled="reduced" :aria-label="reduced ? '减少动态效果模式，使用时间轴浏览' : paused ? '播放二路同步演示' : '暂停二路同步演示'" :aria-pressed="paused" @click="togglePlayback"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path v-if="paused || reduced" d="m9 6 9 6-9 6Z" /><path v-else d="M7 6h3v12H7zm7 0h3v12h-3z" /></svg></button></div>
    <div class="sync-timeline" data-reveal>
      <div class="chapter-heading"><div><span>本段演示</span><h4>{{ chapters[active].label }}</h4></div><output aria-label="演示播放时间">{{ clock(time) }} <span>/ {{ clock(duration) }}</span></output></div>
      <div class="chapter-track">
        <button v-for="(chapter,i) in chapters" :key="chapter.label" class="chapter" :style="{ flex:chapter.end - chapter.start }" :aria-label="'跳转到' + chapter.label" :aria-pressed="active === i" @click="jump(i)">
          <svg viewBox="0 0 28 22" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="2" y="2" width="24" height="18" rx="3" /><path v-if="i === 0" d="M4 5h13v12H4z" fill="currentColor" opacity=".5" /><path v-else-if="i === 1" d="M8 6h12v10H8zM6 4v4m16 6v4" /><path v-else-if="i === 2" d="m8 11 4 4 8-8" /><path v-else d="M7 7h14M7 11h10M7 15h12" /></svg><span>{{ chapter.label }}</span>
        </button>
        <div class="playhead-clip" aria-hidden="true"><div class="playhead-position" :style="{ transform:`translateX(${time / duration * 100}%)` }"><i></i></div></div>
        <input class="timeline-seek" type="range" min="0" :max="duration" step="0.1" :value="time" :aria-valuetext="clock(time) + '，' + chapters[active].label" aria-label="二路同步演示时间轴" @input="seek($event.target.value)" @pointerdown="hold(true)" @pointerup="hold(false)" @pointercancel="hold(false)" />
      </div>
      <p class="chapter-description">{{ chapters[active].description }}</p>
    </div>
  </div>
</template>

<style scoped>
.match-sync-demo { --sync-accent:var(--vp-c-brand-1); min-width:0; }
.sync-screen { position:relative; aspect-ratio:16/9; overflow:hidden; border:1px solid var(--vp-c-divider); border-radius:12px; background:#101114; }
.sync-software { display:block; width:100%; height:100%; object-fit:contain; }
.recording-controls { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:10px; color:var(--vp-c-text-2); font-size:12px; }
.screen-playback { display:grid; place-items:center; width:32px; height:32px; border-radius:50%; background:var(--button-surface); color:var(--vp-c-text-1); }
.screen-playback:disabled { opacity:.45; }
.chapter-heading { display:flex; justify-content:space-between; align-items:end; gap:20px; margin:22px 0 20px; }
.chapter-heading > div > span { color:var(--vp-c-text-2); font-size:12px; }
.chapter-heading h4 { margin:5px 0 0; font-size:20px; font-weight:600; }
.chapter-heading > output { font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.chapter-heading output span { color:var(--vp-c-text-2); }
.chapter-track { position:relative; display:flex; gap:6px; }
.chapter { display:flex; min-width:0; flex-direction:column; justify-content:center; align-items:start; gap:12px; min-height:88px; padding:14px; border:1px solid var(--vp-c-divider); border-radius:12px; color:var(--vp-c-text-2); background:var(--vp-c-bg-alt); text-align:left; }
.chapter svg { width:28px; height:22px; }
.chapter > span { font-size:13px; font-weight:500; }
.chapter[aria-pressed=true] { border-color:var(--sync-accent); background:color-mix(in srgb,var(--sync-accent) 16%,var(--vp-c-bg-alt)); color:var(--vp-c-text-1); }
.playhead-clip { position:absolute; inset:-8px 0 0; overflow:hidden; pointer-events:none; }
.playhead-position { position:absolute; left:8px; right:8px; top:0; bottom:0; }
.playhead-position > i { position:absolute; left:0; top:4px; bottom:0; border-left:2px solid var(--vp-c-text-1); }
.playhead-position > i::before { content:''; position:absolute; top:-4px; left:-5px; width:8px; height:8px; border-radius:50%; background:var(--vp-c-text-1); }
.timeline-seek { position:absolute; z-index:2; left:0; top:-12px; width:100%; height:24px; margin:0; opacity:0; cursor:ew-resize; }
.chapter-track:has(.timeline-seek:focus-visible) { outline:2px solid var(--sync-accent); outline-offset:5px; border-radius:12px; }
.chapter-description { min-height:42px; margin:18px 0 0; color:var(--vp-c-text-2); font-size:14px; line-height:1.8; }

@media (max-width:700px) {
  .chapter { padding:9px 4px; gap:8px; min-height:78px; }
  .chapter > span { font-size:10px; white-space:nowrap; }
  .chapter-heading h4 { font-size:18px; }
}
</style>
