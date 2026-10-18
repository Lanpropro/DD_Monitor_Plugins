<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { withBase } from 'vitepress'

const props = defineProps({ plugin: { type: Object, required: true } })
const root = ref(null)
const phase = ref(0)
const paused = ref(false)
const playing = ref(false)
const animating = ref(false)
const isSync = computed(() => props.plugin.id === 'match_sync')
const phaseLabels = computed(() => isSync.value ? ['选择主画面', '自动对齐', '混音与弹幕'] : props.plugin.features)
const phaseDescription = computed(() => props.plugin.featureDetails[isSync.value ? [1,0,2][phase.value] : phase.value])
const screenshot = computed(() => !isSync.value && phase.value === 2 ? 'danmaku.webp' : 'landscape.webp')
let media, observer, timer, visible = false, focused = false, hovering = false
function sync() {
  clearTimeout(timer)
  animating.value = visible && !paused.value && !media?.matches && !document.hidden
  playing.value = animating.value && !focused && !hovering
  if (playing.value) timer = setTimeout(() => { phase.value = (phase.value + 1) % 3; sync() }, 3500)
}
function selectPhase(index) { if (index >= 0 && index < 3) { phase.value = index; sync() } }
function togglePlayback() { paused.value = !paused.value; sync() }
function onEnter(event) { if (event.pointerType === 'mouse') { hovering = true; sync() } }
function onLeave() { hovering = false; sync() }
function onFocus(event) { focused = event.target.matches(':focus-visible'); sync() }
function onBlur(event) { if (!event.currentTarget.contains(event.relatedTarget)) { focused = false; sync() } }
onMounted(() => {
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; sync() }, { threshold:.2 })
    observer.observe(root.value)
  }
  media.addEventListener('change', sync)
  document.addEventListener('visibilitychange', sync)
})
onUnmounted(() => { clearTimeout(timer); observer?.disconnect(); media?.removeEventListener('change', sync); document.removeEventListener('visibilitychange', sync) })
</script>

<template>
  <div ref="root" class="plugin-motion-demo" role="group" :aria-label="plugin.name + '功能动效示意'" @pointerenter="onEnter" @pointerleave="onLeave" @focusin="onFocus" @focusout="onBlur">
    <div class="motion-heading"><span>功能动效示意</span><button :aria-label="paused ? '播放插件功能演示' : '暂停插件功能演示'" :aria-pressed="paused" @click="togglePlayback"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><path v-if="paused" d="m9 6 9 6-9 6Z" /><path v-else d="M7 6h3v12H7zm7 0h3v12h-3z" /></svg></button></div>
    <div class="motion-stage" :class="['phase-' + phase, { 'is-playing':animating }]">
      <div class="software-snapshot"><img :src="withBase('/screenshots/' + screenshot)" alt="DD 监控室 CE 真实软件界面" width="960" height="540" loading="lazy" /><span class="snapshot-label">{{ isSync ? '主画面位置示意' : plugin.features[phase] }}</span><span v-if="isSync" class="main-frame-highlight" aria-hidden="true"></span></div>
      <div v-if="isSync" class="sync-visual">
        <div class="timeline"><span>主画面 <b>12:08</b></span><div class="timeline-track"><i class="timeline-offset is-main"><em></em></i></div></div>
        <div class="timeline"><span>二路解说 <b>{{ phase === 0 ? '12:05' : '12:08' }}</b></span><div class="timeline-track"><i class="timeline-offset is-secondary"><em></em></i></div></div>
        <div class="sync-status"><span class="sync-dot"></span>{{ phase === 0 ? '选择比赛主画面' : phase === 1 ? '共同画面对齐' : '混音与弹幕汇合' }}</div>
        <div class="mix-preview" :class="{ 'is-mixed': phase === 2 }"><div class="audio-levels" aria-hidden="true"><span v-for="n in 7" :key="n" :style="{ '--bar':n }"></span></div><span>主路 + 二路解说</span><div class="chat-preview"><span><b>A</b> 比赛画面同步</span><span><b>B</b> 多路解说同屏</span></div></div>
      </div>
      <div v-else class="platform-visual"><div class="demo-platforms"><img v-for="(icon,i) in plugin.icons" :key="icon" :src="withBase('/platforms/' + icon)" :alt="plugin.platforms[i]" width="26" height="26" /></div><div class="quality-track"><span :style="{ transform: `scaleX(${phase === 0 ? .8 : phase === 1 ? .25 : 1})` }"></span></div><strong>{{ phase === 0 ? (plugin.id === 'global_live' ? '自动调节画质' : '选择实际画质') : phase === 1 ? '静音悬停预览' : '聊天跟随主画面' }}</strong><div class="chat-preview" :class="{ 'is-chatting':phase === 2 }"><span><b>主播</b> 直播与聊天同屏</span><span><b>观众</b> 关注喜欢的频道</span></div></div>
    </div>
    <div class="motion-phases" role="group" aria-label="演示步骤"><button v-for="(label,i) in phaseLabels" :key="label" :aria-pressed="phase === i" @click="selectPhase(i)"><span>0{{ i + 1 }}</span>{{ label }}</button></div>
    <p class="motion-description">{{ phaseDescription }}</p>
  </div>
</template>

<style scoped>
.plugin-motion-demo { min-width:0; }
.motion-heading { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; color:var(--vp-c-text-2); font-size:12px; }
.motion-heading button { display:grid; place-items:center; width:32px; height:32px; border-radius:50%; background:var(--button-surface); color:var(--vp-c-text-1); }
.motion-stage { display:grid; grid-template-columns:1.2fr 1fr; align-items:center; gap:18px; padding:14px; border:1px solid var(--vp-c-divider); border-radius:12px; background:var(--vp-c-bg); }
.software-snapshot { position:relative; min-width:0; overflow:hidden; border:1px solid var(--vp-c-divider); border-radius:7px; }
.software-snapshot img { display:block; width:100%; height:auto; aspect-ratio:16/9; object-fit:contain; }
.snapshot-label { position:absolute; left:6px; top:6px; padding:3px 6px; border-radius:4px; color:#fff; background:#101418c9; font-size:9px; }
.main-frame-highlight { position:absolute; left:4%; top:16%; width:62%; height:58%; border:2px solid var(--vp-c-brand-1); border-radius:3px; }
.sync-visual,.platform-visual { min-width:0; }
.timeline + .timeline { margin-top:10px; }
.timeline > span { display:flex; justify-content:space-between; color:var(--vp-c-text-2); font-size:10px; }
.timeline b { color:var(--vp-c-text-1); font-weight:500; font-variant-numeric:tabular-nums; }
.timeline-track { position:relative; height:17px; margin-top:5px; overflow:hidden; border-bottom:1px solid var(--vp-c-divider); background:repeating-linear-gradient(90deg,transparent 0 15px,var(--vp-c-divider) 15px 16px); }
.timeline-offset { position:absolute; inset:0; transform:translateX(48%); transition:transform 600ms var(--ease-out); }
.timeline-offset em { display:block; width:3px; height:100%; background:var(--vp-c-brand-1); border-radius:2px; }
.phase-0 .timeline-offset.is-secondary { transform:translateX(20%); }
.sync-status { display:flex; align-items:center; gap:5px; margin:10px 0; font-size:10px; color:var(--vp-c-brand-1); }
.sync-dot { width:5px; height:5px; border-radius:50%; background:currentColor; }
.mix-preview { opacity:.3; transition:opacity 200ms ease; font-size:9px; }
.mix-preview.is-mixed { opacity:1; }
.audio-levels { display:inline-flex; gap:2px; align-items:center; height:15px; margin-right:6px; vertical-align:middle; }
.audio-levels span { width:3px; height:100%; border-radius:2px; background:var(--vp-c-brand-1); transform:scaleY(.35); }
.is-playing.phase-2 .audio-levels span { animation:audio-level 800ms ease-in-out infinite alternate; animation-delay:calc(var(--bar) * -110ms); }
@keyframes audio-level { from { transform:scaleY(.25); } to { transform:scaleY(.95); } }
.chat-preview { display:grid; gap:4px; margin-top:8px; opacity:.3; transition:opacity 200ms ease; }
.is-mixed .chat-preview,.chat-preview.is-chatting { opacity:1; }
.chat-preview span { padding:4px 6px; border-radius:4px; background:var(--vp-c-bg-soft); color:var(--vp-c-text-2); font-size:9px; }
.chat-preview b { color:var(--vp-c-brand-1); margin-right:4px; }
.demo-platforms { display:flex; gap:10px; margin-bottom:20px; }
.demo-platforms img { object-fit:contain; }
.quality-track { height:5px; margin-bottom:12px; border-radius:4px; background:var(--vp-c-divider); overflow:hidden; }
.quality-track span { display:block; height:100%; background:var(--vp-c-brand-1); transform-origin:left; transition:transform 600ms var(--ease-out); }
.platform-visual > strong { font-size:12px; font-weight:500; }
.motion-phases { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; margin-top:12px; }
.motion-phases button { min-width:0; min-height:42px; padding:7px; border-radius:7px; border:1px solid transparent; background:var(--button-surface); text-align:left; font-size:11px; }
.motion-phases button span { margin-right:5px; opacity:.5; font-size:9px; }
.motion-phases button[aria-pressed=true] { border-color:var(--vp-c-brand-1); color:var(--vp-c-brand-1); }
.motion-description { min-height:58px; margin:12px 0 0; color:var(--vp-c-text-2); font-size:12px; line-height:1.8; }
@media (max-width:700px) { .motion-stage { grid-template-columns:1fr; } .motion-phases { grid-template-columns:1fr; } .motion-description { min-height:0; } .snapshot-label { font-size:10px; } .timeline > span,.sync-status { font-size:12px; } .chat-preview span { font-size:11px; } }
@media (prefers-reduced-motion:reduce) { .timeline-offset,.quality-track span,.mix-preview,.chat-preview { transition:none; } .audio-levels span { animation:none !important; } }
</style>
