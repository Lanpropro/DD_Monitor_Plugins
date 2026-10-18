<script setup>
import { ref } from 'vue'

// Structure follows ddm/skin_preview.py::_main; presets follow ddm/layouts.py.
const active = ref(0)
const ratio = ref(62)
const light = ref(false)
const equal = ref(false)
const wall = ref(null)
let dragging = false
const features = [
  { name: '调节布局', title: '拖动比例，分清主次', description: '拖动主画面右侧分隔条，或用下方滑块调整比例。缩略预览保留软件的关注栏、六格画面墙和播放控制条。' },
  { name: '切换主题', title: '深浅外观，随手比较', description: '切换缩略预览的深浅色，查看关注栏、卡片和控制条的变化。配色参考软件「设置 → 外观」。' },
  { name: '观看组合', title: '主次布局，或六路平分', description: '在「主画面 + 5 小」和「六路平分」之间切换，比较两种观看组合。' }
]
function setRatio(value) { ratio.value = Math.min(68, Math.max(35, Math.round(value))) }
function fromPointer(clientX) {
  const rect = wall.value?.getBoundingClientRect()
  if (rect?.width) setRatio((clientX - rect.left) / rect.width * 100)
}
function onPointerDown(event) {
  dragging = true
  event.currentTarget.setPointerCapture?.(event.pointerId)
  fromPointer(event.clientX)
}
function onPointerMove(event) { if (dragging) fromPointer(event.clientX) }
function onPointerUp(event) { dragging = false; event.currentTarget.releasePointerCapture?.(event.pointerId) }
function onGripKey(event) {
  if (event.key === 'ArrowLeft') setRatio(ratio.value - (event.shiftKey ? 5 : 1))
  else if (event.key === 'ArrowRight') setRatio(ratio.value + (event.shiftKey ? 5 : 1))
  else return
  event.preventDefault()
}
</script>

<template>
  <div class="feature-demo">
    <div class="demo-selector">
      <div class="demo-tabs" role="group" aria-label="软件功能演示">
        <button v-for="(feature, i) in features" :key="feature.name" :aria-pressed="active === i" @click="active = i"><span>0{{ i + 1 }}</span>{{ feature.name }}</button>
      </div>
      <div class="demo-description" aria-live="polite"><h3>{{ features[active].title }}</h3><p>{{ features[active].description }}</p></div>
    </div>
    <div class="demo-preview">
      <div class="demo-toolbar"><span>DD 监控室 CE</span><span>外观缩略预览</span></div>
      <div class="software-miniature" :class="{ 'is-light': light }" role="group" aria-label="软件外观缩略预览">
        <aside class="mini-sidebar" aria-hidden="true">
          <div class="mini-brand">DD监控室CE <span>«</span></div>
          <div class="mini-tools"><span>排序</span><span>◐</span><span>↻</span></div>
          <div class="mini-folder">文件夹 · 3</div>
          <div v-for="n in 3" :key="n" class="mini-follow"><i></i><span>关注卡片</span></div>
          <div class="mini-actions"><span>布局预设</span><span>设置</span><span>导入关注</span><span>添加直播间</span></div>
        </aside>
        <div ref="wall" class="mini-wall" :class="{ 'is-equal': active === 2 && equal }"
          :style="{ gridTemplateColumns: active === 2 && equal ? 'repeat(3, 1fr)' : `${ratio / 2}fr ${ratio / 2}fr ${100 - ratio}fr` }">
          <div v-for="n in 6" :key="n" class="mini-tile" :class="{ 'mini-main': n === 1 }">
            <span class="mini-badge" aria-hidden="true"><b>LIVE</b> · {{ n === 1 ? '主画面' : '直播 ' + n }}</span>
            <span class="mini-video-label" aria-hidden="true">直播画面</span>
            <div class="mini-controls" aria-hidden="true"><span>Ⅱ</span><span>♪</span><i></i><span v-if="n === 1">40</span><span>⛶</span></div>
            <span v-if="active === 0 && n === 1" class="demo-grip" role="separator" aria-orientation="vertical" aria-label="拖动调整主画面比例"
              :aria-valuenow="ratio" aria-valuemin="35" aria-valuemax="68" tabindex="0"
              @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" @keydown="onGripKey"></span>
          </div>
        </div>
      </div>
      <div class="demo-controls">
        <label v-if="active === 0" class="ratio-control">主画面比例 <input v-model.number="ratio" type="range" min="35" max="68" aria-label="主画面比例" /><output>{{ ratio }}%</output></label>
        <button v-else-if="active === 1" class="demo-toggle" :aria-pressed="light" @click="light = !light">{{ light ? '切换为深色' : '切换为浅色' }}<span aria-hidden="true">◐</span></button>
        <div v-else class="demo-presets" role="group" aria-label="观看组合示意"><button :aria-pressed="!equal" @click="equal = false">主画面 + 5 小</button><button :aria-pressed="equal" @click="equal = true">六路平分</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.feature-demo { display: grid; grid-template-columns: minmax(240px, .7fr) minmax(0, 1.3fr); gap: 36px; align-items: center; }
.demo-tabs { display: grid; gap: 10px; }
.demo-tabs button { display: flex; align-items: center; gap: 20px; padding: 16px 18px; text-align: left; font-size: 16px; border: 1px solid var(--vp-c-divider); border-radius: 10px; color: var(--vp-c-text-2); background: var(--vp-c-bg-alt); }
.demo-tabs button span { font-size: 12px; opacity: .65; }
.demo-tabs button[aria-pressed=true] { border-color: var(--vp-c-brand-1); color: var(--vp-c-brand-1); background: var(--vp-c-bg-soft); }
.demo-description { margin-top: 26px; }
.demo-description h3 { font-size: 22px; font-weight: 600; margin: 0 0 12px; }
.demo-description p { font-size: 15px; line-height: 1.85; color: var(--vp-c-text-2); }
.demo-preview { min-width: 0; padding: 18px; border: 1px solid var(--vp-c-divider); border-radius: 14px; background: var(--vp-c-bg-alt); }
.demo-toolbar { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 14px; color: var(--vp-c-text-2); font-size: 12px; }
.software-miniature { --mini-bg: #222428; --mini-side: #1d2023; --mini-card: #303338; --mini-text: #f1f2f5; --mini-muted: #aeb6c1; --mini-border: #414851; --mini-overlay: #17202b; display: grid; grid-template-columns: 19.3% minmax(0, 1fr); gap: 7px; padding: 7px; aspect-ratio: 800 / 520; border-radius: 9px; background: var(--mini-bg); color: var(--mini-text); line-height: 1.35; }
.software-miniature.is-light { --mini-bg: #f4f6f9; --mini-side: #ebeef3; --mini-card: #fff; --mini-text: #202630; --mini-muted: #4a5565; --mini-border: #d7dde7; --mini-overlay: #f7f9fc; }
.mini-sidebar { display: flex; flex-direction: column; gap: 5px; min-width: 0; min-height: 0; overflow: hidden; padding: 5px; border-radius: 5px; background: var(--mini-side); font-size: clamp(6px, .75vw, 10px); }
.mini-brand { display: flex; justify-content: space-between; font-weight: 600; white-space: nowrap; }
.mini-tools { display: flex; justify-content: space-between; color: var(--mini-muted); }
.mini-folder { padding: 5px 2px; border-bottom: 1px solid var(--mini-border); }
.mini-follow { flex: 1; display: flex; align-items: center; justify-content: center; gap: 4px; min-height: 0; border-radius: 4px; background: var(--mini-card); color: var(--mini-muted); }
.mini-follow i { width: 11px; height: 11px; border-radius: 50%; background: var(--mini-border); }
.mini-actions { display: grid; grid-template-columns: 1.4fr 1fr; gap: 3px; margin-top: 5px; color: var(--mini-muted); }
.mini-actions span { padding: 3px 0; text-align: center; background: var(--mini-card); border-radius: 3px; white-space: nowrap; }
.mini-wall { display: grid; grid-template-rows: repeat(3, minmax(0, 1fr)); gap: 6px; min-width: 0; }
.mini-tile { position: relative; display: grid; place-items: center; min-width: 0; min-height: 0; border-radius: 5px; background: #101114; border: 1px solid var(--mini-border); }
.mini-main { grid-column: span 2; grid-row: span 2; }
.mini-badge { position: absolute; top: 4px; left: 4px; max-width: calc(100% - 8px); overflow: hidden; white-space: nowrap; padding: 2px 4px; border-radius: 3px; font-size: clamp(5px, .65vw, 9px); background: var(--mini-overlay); color: var(--mini-text); }
.mini-badge b { color: #fb7299; font-size: .9em; }
.is-light .mini-badge b { color: #b93665; }
.mini-video-label { color: #78818b; font-size: clamp(6px, .8vw, 11px); }
.mini-controls { position: absolute; inset: auto 0 0; display: flex; align-items: center; gap: 5px; padding: 4px; background: var(--mini-overlay); color: var(--mini-muted); font-size: clamp(6px, .7vw, 10px); border-radius: 0 0 4px 4px; }
.mini-controls i { flex: 1; height: 2px; background: var(--mini-border); }
.mini-wall.is-equal { grid-template-rows: repeat(2, minmax(0, 1fr)); }
.is-equal .mini-main { grid-column: auto; grid-row: auto; }
.demo-grip { position: absolute; top: 0; bottom: 0; right: -6px; width: 12px; z-index: 2; cursor: col-resize; touch-action: none; }
.demo-grip::after { content: ''; position: absolute; inset: 15% 4px; border-radius: 4px; background: #00a1d6; opacity: .65; }
.demo-grip:hover::after, .demo-grip:focus-visible::after { opacity: 1; }
.demo-controls { min-height: 68px; padding-top: 18px; display: flex; align-items: center; }
.ratio-control { display: flex; align-items: center; gap: 12px; width: 100%; font-size: 13px; }
.ratio-control input { min-width: 0; flex: 1; accent-color: var(--vp-c-brand-1); }
.ratio-control output { min-width: 32px; font-variant-numeric: tabular-nums; }
.demo-toggle { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 10px 14px; border: 1px solid var(--vp-c-divider); border-radius: 8px; }
.demo-presets { display: flex; gap: 10px; }
.demo-presets button { font-size: 13px; padding: 10px 12px; border: 1px solid var(--vp-c-divider); border-radius: 8px; }
.demo-presets button[aria-pressed=true] { color: var(--vp-c-brand-1); border-color: var(--vp-c-brand-1); }
@media (max-width: 900px) { .feature-demo { grid-template-columns: 1fr; gap: 24px; } .demo-tabs { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; } .demo-tabs button { justify-content: center; font-size: 13px; padding: 12px 7px; } .demo-tabs button span { display: none; } }
@media (max-width: 480px) { .demo-preview { padding: 12px; } .software-miniature { gap: 4px; padding: 4px; } .mini-wall { gap: 4px; } .mini-sidebar { padding: 3px; gap: 3px; } .mini-follow i { display: none; } .mini-controls { gap: 3px; padding: 3px; } .mini-badge { top: 2px; left: 2px; padding: 1px 2px; } .ratio-control { font-size: 12px; gap: 8px; } }
</style>
