<script setup>
import { ref } from 'vue'

const active = ref(0)
const ratio = ref(62)
const light = ref(false)
const equal = ref(false)
const wall = ref(null)
const RATIO_MIN = 35
const RATIO_MAX = 68
let dragging = false

function clampRatio(value) {
  return Math.min(RATIO_MAX, Math.max(RATIO_MIN, Math.round(value)))
}
function ratioFromPointer(clientX) {
  const rect = wall.value?.getBoundingClientRect()
  if (!rect || !rect.width) return
  ratio.value = clampRatio(((clientX - rect.left) / rect.width) * 100)
}
function onPointerDown(event) {
  dragging = true
  event.currentTarget.setPointerCapture?.(event.pointerId)
  ratioFromPointer(event.clientX)
}
function onPointerMove(event) {
  if (dragging) ratioFromPointer(event.clientX)
}
function onPointerUp(event) {
  dragging = false
  event.currentTarget.releasePointerCapture?.(event.pointerId)
}
function onGripKey(event) {
  const step = event.shiftKey ? 5 : 1
  if (event.key === 'ArrowLeft') ratio.value = clampRatio(ratio.value - step)
  else if (event.key === 'ArrowRight') ratio.value = clampRatio(ratio.value + step)
  else return
  event.preventDefault()
}

const features = [
  { title: '拖动比例，分清主次', name: '调节布局', description: '直接拖动画面之间的分隔条，或用下方滑块微调。软件中同样可以拖动预设布局的边框，双击恢复默认比例。' },
  { title: '换个主题，继续看直播', name: '切换主题', description: '试试深浅色切换。0.4 支持跟随系统，也能导入本地图片或静音视频背景。' },
  { title: '一组直播，一次恢复', name: '观看组合', description: '切换两种组合示意。软件可将布局比例、主播位置和每格声音保存为自己的直播间组合。' }
]
</script>

<template>
  <div class="feature-demo">
    <div class="demo-selector">
      <div class="demo-tabs" role="group" aria-label="软件功能演示">
        <button v-for="(feature, i) in features" :key="feature.name" :aria-pressed="active === i" @click="active = i"><span>0{{ i + 1 }}</span>{{ feature.name }}</button>
      </div>
      <div class="demo-description" aria-live="polite"><h3>{{ features[active].title }}</h3><p>{{ features[active].description }}</p></div>
    </div>
    <div class="demo-preview" :class="{ 'is-light': light && active === 1 }">
      <div class="demo-toolbar"><span>DD 监控室 CE</span><span>交互示意</span></div>
      <div ref="wall" class="demo-wall" :class="{ 'is-equal': active === 2 && equal }" :style="{ gridTemplateColumns: active === 2 && equal ? 'repeat(3, 1fr)' : (active === 0 ? ratio : 62) + '% 1fr' }">
        <div class="demo-tile demo-main" :class="{ 'is-splittable': active === 0 }"><span class="tile-number">01</span><span>主画面</span><span class="tile-control">音量 · 70%</span>
          <span v-if="active === 0" class="demo-grip" role="separator" aria-orientation="vertical" aria-label="拖动调整主画面比例"
            :aria-valuenow="ratio" :aria-valuemin="RATIO_MIN" :aria-valuemax="RATIO_MAX" tabindex="0"
            @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" @keydown="onGripKey"></span>
        </div>
        <div class="demo-tile"><span class="tile-number">02</span><span>二路直播</span><span class="tile-control">静音</span></div>
        <div class="demo-tile"><span class="tile-number">03</span><span>二路直播</span><span class="tile-control">静音</span></div>
      </div>
      <div class="demo-controls">
        <label v-if="active === 0" class="ratio-control">主画面比例 <input v-model.number="ratio" type="range" min="35" max="68" aria-label="主画面比例" /><output>{{ ratio }}%</output></label>
        <button v-else-if="active === 1" class="demo-toggle" :aria-pressed="light" @click="light = !light">{{ light ? '切换为深色' : '切换为浅色' }}<span aria-hidden="true">◐</span></button>
        <div v-else class="demo-presets" role="group" aria-label="观看组合示意"><button :aria-pressed="!equal" @click="equal = false">主画面 + 两路</button><button :aria-pressed="equal" @click="equal = true">三路平分</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.feature-demo { display: grid; grid-template-columns: .8fr 1.2fr; gap: 48px; align-items: center; }
.demo-tabs { display: grid; gap: 10px; }
.demo-tabs button { display: flex; align-items: center; gap: 20px; padding: 16px 18px; text-align: left; font-size: 16px; border: 1px solid transparent; border-radius: 8px; color: var(--vp-c-text-2); background: var(--vp-c-bg-alt); transition: border-color 160ms ease, color 160ms ease; }
.demo-tabs button span { font-size: 12px; opacity: .65; }
.demo-tabs button[aria-pressed=true] { border-color: var(--vp-c-brand-1); color: var(--vp-c-brand-1); }
.demo-description { margin: 28px 0 0; }
.demo-description h3 { font-size: 22px; font-weight: 600; margin: 0 0 12px; }
.demo-description p { font-size: 16px; line-height: 1.85; color: var(--vp-c-text-2); }
.demo-preview { --demo-bg: #111a23; --demo-tile: #243744; --demo-text: #dcebf3; --demo-muted: #a8c0cd; background: var(--demo-bg); color: var(--demo-text); border: 1px solid var(--vp-c-divider); border-radius: 12px; padding: 18px; box-shadow: 0 18px 50px #0002; }
.demo-preview.is-light { --demo-bg: #edf3f6; --demo-tile: #d0e0e9; --demo-text: #203f51; --demo-muted: #456374; }
.demo-toolbar { display: flex; justify-content: space-between; margin-bottom: 16px; color: var(--demo-muted); font-size: 12px; }
.demo-wall { display: grid; grid-template-rows: 1fr 1fr; gap: 8px; aspect-ratio: 16 / 9; }
.demo-tile { display: flex; flex-direction: column; justify-content: space-between; padding: 14px; background: var(--demo-tile); border: 1px solid #72b5d32b; border-radius: 6px; font-size: 14px; line-height: 1.4; min-width: 0; overflow: hidden; }
.demo-main { grid-row: span 2; border-color: #4ab2ce; background: color-mix(in srgb, var(--demo-tile) 80%, #087e99); position: relative; }
.is-splittable { overflow: visible; }
.demo-grip { position: absolute; top: 0; bottom: 0; right: -5px; width: 10px; z-index: 2; cursor: col-resize; touch-action: none; }
.demo-grip::after { content: ''; position: absolute; inset: 14px 3px; border-radius: 999px; background: #43c4df; opacity: 0; transition: opacity 160ms ease; }
.demo-grip:hover::after, .demo-grip:focus-visible::after { opacity: .85; }
.demo-grip:focus-visible { outline: 2px solid #43c4df; outline-offset: 1px; border-radius: 4px; }
.is-equal { grid-template-rows: 1fr; }
.is-equal .demo-main { grid-row: auto; }
.tile-number { font-size: 24px; line-height: 1; font-variant-numeric: tabular-nums; opacity: .6; }
.tile-control { font-size: 12px; line-height: 1.3; color: var(--demo-muted); }
.demo-controls { min-height: 64px; padding-top: 20px; display: flex; align-items: center; }
.ratio-control { display: flex; align-items: center; gap: 16px; width: 100%; font-size: 13px; }
.ratio-control input { min-width: 0; flex: 1; cursor: ew-resize; accent-color: #43c4df; }
.ratio-control output { min-width: 32px; font-variant-numeric: tabular-nums; }
.demo-toggle { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 10px 14px; border: 1px solid #7196aa60; border-radius: 6px; font-size: 14px; }
.demo-toggle span { font-size: 20px; }
.demo-presets { display: flex; gap: 10px; }
.demo-presets button { font-size: 13px; padding: 9px 12px; border: 1px solid #7196aa60; border-radius: 6px; }
.demo-presets button[aria-pressed=true] { border-color: #43c4df; }
@media (max-width: 900px) { .feature-demo { grid-template-columns: 1fr; gap: 24px; } .demo-tabs { grid-template-columns: repeat(3, 1fr); gap: 6px; } .demo-tabs button { justify-content: center; font-size: 14px; padding: 12px 8px; } .demo-tabs button span { display: none; } }
@media (max-width: 480px) { .demo-preview { padding: 12px; } .demo-tile { padding: 9px; font-size: 12px; } .tile-number { font-size: 19px; } .tile-control { font-size: 12px; } .ratio-control { gap: 10px; font-size: 12px; } }
@media (max-width: 480px) { .demo-wall:not(.is-equal) .demo-tile:not(.demo-main) .tile-number { display: none; } }
@media (prefers-reduced-motion: no-preference) { .demo-preview { transition: background-color 200ms ease, color 200ms ease; } .demo-wall .demo-tile { transition: background-color 200ms ease; } }
</style>
