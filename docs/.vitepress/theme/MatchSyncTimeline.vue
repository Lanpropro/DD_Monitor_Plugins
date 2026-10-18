<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { withBase } from 'vitepress'

const chapters = [
  { label:'选择主画面', start:0, end:4, description:'点击上方主画面，将比赛直播设为所有二路的对齐基准。' },
  { label:'框选计时区域', start:4, end:8, description:'点击画面中的虚线框，选择两路共同的比赛计时区域。' },
  { label:'自动对齐', start:8, end:13, description:'比较共同比赛画面，逐步吸收二路延迟；拖动时间轴可查看对齐过程。' },
  { label:'混合解说', start:13, end:17, description:'拖动上方的两路音量滑块，调整主画面与二路解说的模拟混音比例。' },
  { label:'合并弹幕', start:17, end:22, description:'带来源标记的弹幕汇入同一画面，也可以点击按钮切换为分路查看。' }
]
const duration = 22
const root = ref(null)
const time = ref(0)
const paused = ref(false)
const reduced = ref(false)
const playing = ref(false)
const mainVolume = ref(70)
const secondaryVolume = ref(40)
const merged = ref(true)
const active = computed(() => {
  const index = chapters.findIndex(chapter => time.value < chapter.end)
  return index < 0 ? 4 : index
})
const alignment = computed(() => Math.min(1, Math.max(0, (time.value - 8) / 5)))
const delay = computed(() => 3.2 * (1 - alignment.value))
const clock = value => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2,'0')}`
let media, observer, frame = 0, last = 0, visible = false, held = false, focused = false
function sync() {
  cancelAnimationFrame(frame)
  frame = 0
  last = 0
  playing.value = visible && !paused.value && !reduced.value && !held && !focused && !document.hidden
  if (playing.value) frame = requestAnimationFrame(tick)
}
function tick(now) {
  if (last) time.value = (time.value + Math.min(now - last, 100) / 1000) % duration
  last = now
  frame = requestAnimationFrame(tick)
}
function seek(value) { time.value = Math.max(0, Math.min(duration, Number(value))); sync() }
function jump(index) { if (chapters[index]) seek(chapters[index].start) }
function togglePlayback() {
  if (reduced.value) return
  paused.value = !paused.value
  if (!paused.value && time.value === duration) time.value = 0
  sync()
}
function hold(value) { held = value; sync() }
function onFocus(event) { focused = event.target.matches('.timeline-seek:focus-visible, .mixer-card input:focus-visible'); sync() }
function onBlur(event) { if (!event.currentTarget.contains(event.relatedTarget)) { focused = false; sync() } }
function preferenceChanged() { reduced.value = media.matches; sync() }
onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; sync() }, { threshold:.2 })
    observer.observe(root.value)
  }
  preferenceChanged()
  media.addEventListener('change', preferenceChanged)
  document.addEventListener('visibilitychange', sync)
})
onUnmounted(() => { cancelAnimationFrame(frame); observer?.disconnect(); media?.removeEventListener('change', preferenceChanged); document.removeEventListener('visibilitychange', sync) })
</script>

<template>
  <div ref="root" class="match-sync-demo" role="group" aria-label="比赛二路同步交互时间轴演示" aria-live="off" @focusin="onFocus" @focusout="onBlur">
    <div class="sync-screen" data-reveal :class="{ 'is-playing':playing }">
      <img class="sync-software" :src="withBase('/screenshots/landscape.webp')" alt="DD 监控室 CE 软件界面，叠加可操作的二路同步演示" width="1920" height="1080" loading="lazy" />
      <div class="screen-caption"><span>DD 监控室 CE · 比赛二路同步</span><span>交互演示</span></div>
      <button v-if="active === 0" class="main-view-target" aria-label="设为比赛主画面" @click="jump(1)"><span>点击设为主画面</span></button>
      <div v-if="active >= 1" class="main-view-outline" aria-hidden="true"></div>
      <button v-if="active >= 1 && active <= 2" class="roi-target" aria-label="框选共同计时区域" @click="jump(2)"><span>共同计时区域</span><strong>{{ clock(728 + time) }}</strong></button>
      <div v-if="active <= 2" class="comparison-card"><span>主画面 <b>{{ clock(728 + time) }}</b></span><span>二路解说 <b>{{ clock(728 + time - delay) }}</b></span><p :class="{ aligned:alignment === 1 }">{{ alignment === 1 ? '共同画面已对齐' : '延迟差 ' + delay.toFixed(1) + 's' }}</p><i><em :style="{ transform:`scaleX(${alignment})` }"></em></i></div>
      <div v-if="active === 3" class="mixer-card">
        <strong>混合解说 <span>模拟音量</span></strong>
        <label>主画面 <output>{{ mainVolume }}%</output><input v-model.number="mainVolume" aria-label="主画面模拟音量" type="range" min="0" max="100" @pointerdown="hold(true)" @pointerup="hold(false)" @pointercancel="hold(false)" /></label>
        <label>二路解说 <output>{{ secondaryVolume }}%</output><input v-model.number="secondaryVolume" aria-label="二路解说模拟音量" type="range" min="0" max="100" @pointerdown="hold(true)" @pointerup="hold(false)" @pointercancel="hold(false)" /></label>
        <div class="mix-levels" aria-hidden="true"><i :style="{ transform:`scaleX(${mainVolume/100})` }"></i><i :style="{ transform:`scaleX(${secondaryVolume/100})` }"></i></div>
      </div>
      <div v-if="active === 4" class="comments-card" :class="{ separated:!merged }"><div><strong>{{ merged ? '合并弹幕' : '分路弹幕' }}</strong><button :aria-pressed="merged" @click="merged = !merged">{{ merged ? '分路查看' : '合并查看' }}</button></div><p><b>主画面</b> 这波配合漂亮</p><p><b>二路 A</b> 一起看这次团战</p><p><b>二路 B</b> 比赛画面同步到达</p></div>
      <button class="screen-playback" :disabled="reduced" :aria-label="reduced ? '减少动态效果模式，使用时间轴浏览' : paused ? '播放二路同步演示' : '暂停二路同步演示'" :aria-pressed="paused" @click="togglePlayback"><svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path v-if="paused || reduced" d="m9 6 9 6-9 6Z" /><path v-else d="M7 6h3v12H7zm7 0h3v12h-3z" /></svg></button>
    </div>
    <div class="sync-timeline" data-reveal>
    <div class="chapter-heading"><div><span>本段演示</span><h4>{{ chapters[active].label }}</h4></div><output aria-label="演示播放时间">{{ clock(time) }} <span>/ {{ clock(duration) }}</span></output></div>
    <div class="chapter-track">
      <button v-for="(chapter,i) in chapters" :key="chapter.label" class="chapter" :style="{ flex:chapter.end - chapter.start }" :aria-label="'跳转到' + chapter.label" :aria-pressed="active === i" @click="jump(i)">
        <svg viewBox="0 0 28 22" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="2" y="2" width="24" height="18" rx="3" /><path v-if="i === 0" d="M4 5h13v12H4z" fill="currentColor" opacity=".5" /><path v-else-if="i === 1" d="M8 6h12v10H8zM6 4v4m16 6v4" /><path v-else-if="i === 2" d="m8 11 4 4 8-8" /><path v-else-if="i === 3" d="M8 10v4m4-7v10m4-8v6m4-5v4" /><path v-else d="M7 7h14M7 11h10M7 15h12" /></svg><span>{{ chapter.label }}</span>
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
.sync-screen { position:relative; aspect-ratio:16/9; overflow:hidden; border:1px solid var(--vp-c-divider); border-radius:16px; background:#101114; }
.sync-software { display:block; width:100%; height:100%; object-fit:contain; }
.screen-caption { position:absolute; inset:0 0 auto; display:flex; justify-content:space-between; gap:12px; padding:14px 18px; color:#eef1f8; background:linear-gradient(#10131dee,transparent); font-size:12px; pointer-events:none; }
.screen-caption > span:last-child { color:#9ed9ed; }
.main-view-target,.main-view-outline { position:absolute; left:3%; top:12%; width:63%; height:61%; border:2px solid var(--sync-accent); border-radius:8px; background:transparent; }
.main-view-target { cursor:pointer; color:#fff; }
.main-view-target span { position:absolute; left:12px; top:12px; padding:7px 10px; border-radius:7px; background:#1d2023ed; font-size:13px; }
.main-view-outline { pointer-events:none; opacity:.5; }
.roi-target { position:absolute; left:6%; top:23%; width:24%; min-height:76px; display:grid; justify-content:center; align-content:center; gap:5px; border:2px dashed #9ed9ed; border-radius:7px; background:#1d2023ed; color:#fff; cursor:crosshair; }
.roi-target span { font-size:11px; color:#9ed9ed; }
.roi-target strong { font-size:24px; font-variant-numeric:tabular-nums; }
.comparison-card { position:absolute; right:3%; top:14%; width:25%; padding:14px; border:1px solid #ffffff20; border-radius:12px; background:#1d2023ed; color:#bfc6d7; backdrop-filter:blur(12px); font-size:11px; }
.comparison-card > span { display:flex; justify-content:space-between; gap:8px; margin-bottom:10px; }
.comparison-card b { color:#fff; font-weight:500; font-variant-numeric:tabular-nums; }
.comparison-card p { margin:12px 0 8px; color:#9ed9ed; font-size:12px; }
.comparison-card p.aligned { color:#7ce4cf; }
.comparison-card > i { display:block; height:3px; overflow:hidden; border-radius:3px; background:#ffffff18; }
.comparison-card em { display:block; height:100%; background:var(--sync-accent); transform-origin:left; }
.mixer-card,.comments-card { position:absolute; right:4%; bottom:13%; width:35%; padding:16px; border:1px solid #ffffff20; border-radius:14px; background:#1d2023ed; color:#f1f2f8; backdrop-filter:blur(16px); }
.mixer-card > strong { display:flex; justify-content:space-between; font-size:13px; }
.mixer-card > strong span { color:#919aae; font-size:10px; font-weight:400; }
.mixer-card label { display:block; margin-top:14px; font-size:11px; }
.mixer-card output { float:right; font-variant-numeric:tabular-nums; }
.mixer-card input { display:block; width:100%; margin-top:8px; accent-color:var(--sync-accent); }
.mix-levels { display:grid; gap:4px; margin-top:12px; }
.mix-levels i { display:block; height:3px; background:var(--sync-accent); transform-origin:left; }
.comments-card > div { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:14px; }
.comments-card strong { font-size:13px; }
.comments-card button { padding:4px 7px; border-radius:5px; color:#a9e3f5; background:#ffffff10; font-size:10px; }
.comments-card p { margin:8px 0 0; font-size:11px; }
.comments-card p b { display:inline-block; margin-right:6px; padding:2px 5px; border-radius:4px; color:#a9e3f5; background:#00a1d629; font-size:10px; }
.comments-card.separated p { padding:6px; border:1px solid #ffffff20; border-radius:5px; }
.screen-playback { position:absolute; right:18px; bottom:18px; display:grid; place-items:center; width:44px; height:44px; border-radius:50%; background:#303338d9; color:#fff; backdrop-filter:blur(12px); }
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
@media (hover:hover) and (pointer:fine) { .chapter:hover { border-color:var(--sync-accent); } .screen-playback:hover { background:#3b3e42; } }
@media (max-width:700px) {
  .sync-screen { aspect-ratio:4/3; border-radius:14px; }
  .sync-software { object-fit:cover; }
  .screen-caption { padding:10px; font-size:9px; }
  .main-view-target,.main-view-outline { top:18%; height:53%; }
  .main-view-target span { left:6px; top:6px; padding:5px; font-size:10px; }
  .comparison-card { top:20%; width:37%; padding:9px; font-size:9px; }
  .comparison-card p { font-size:10px; }
  .roi-target { left:5%; top:38%; width:34%; min-height:60px; }
  .roi-target strong { font-size:18px; }
  .roi-target span { font-size:9px; }
  .mixer-card,.comments-card { right:5%; bottom:17%; width:65%; padding:12px; }
  .mixer-card label { margin-top:10px; }
  .comments-card > div { margin-bottom:8px; }
  .comments-card p { font-size:10px; }
  .screen-playback { right:10px; bottom:10px; width:36px; height:36px; }
  .chapter-heading { margin-top:16px; }
  .chapter-heading h4 { font-size:18px; }
  .chapter { padding:7px 5px; min-height:76px; gap:9px; border-radius:8px; }
  .chapter svg { width:23px; height:18px; }
  .chapter > span { font-size:10px; line-height:1.4; }
  .chapter-description { font-size:12px; }
}
</style>
