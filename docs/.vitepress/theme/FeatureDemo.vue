<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { withBase } from 'vitepress'
import { TOUR_DURATION, viewingTourFrame } from './viewing-tour.mjs'

// Preset cells mirror ddm/layouts.py: [row, column, rowSpan, columnSpan].
const even = (rows, cols) => Array.from({ length: rows * cols }, (_, i) => [Math.floor(i / cols), i % cols, 1, 1])
const presets = [
  { id: 'corner', name: '主画面 + 5 小', rows: 3, cols: 3, cells: [[0,0,2,2],[0,2,1,1],[1,2,1,1],[2,0,1,1],[2,1,1,1],[2,2,1,1]] },
  { id: '3x2', name: '六分', rows: 2, cols: 3, cells: even(2,3) },
  { id: '2x2', name: '四分', rows: 2, cols: 2, cells: even(2,2) },
  { id: '1x2', name: '左右两分', rows: 1, cols: 2, cells: even(1,2) },
  { id: '1x1', name: '单画面', rows: 1, cols: 1, cells: even(1,1) },
  { id: '3x3', name: '九分', rows: 3, cols: 3, cells: even(3,3) },
  { id: '4x4', name: '十六分', rows: 4, cols: 4, cells: even(4,4) },
  { id: 'main2', name: '主画面 + 2 小', rows: 2, cols: 3, cells: [[0,0,2,2],[0,2,1,1],[1,2,1,1]] }
]
const cards = [
  { id: 'a', name: '演示主播 A', image: '01.webp' },
  { id: 'b', name: '演示主播 B', image: '02.webp' },
  { id: 'c', name: '演示主播 C', image: '03.webp' }
]
const presetId = ref('corner')
const preset = computed(() => presets.find(item => item.id === presetId.value))
const light = ref(false)
const pickerOpen = ref(false)
const slots = ref(Array.from({ length: 16 }, (_, i) => i < 6 ? cards[i % cards.length] : null))
const selected = ref(null)
const root = ref(null)
const dragged = ref(null)
const over = ref(-1)
const point = ref({ x: 0, y: 0 })
const landed = ref(-1)
const placement = ref(0)
const status = ref('把关注卡片拖入画面，或先选择卡片再点击画面。')
let cardStart = null
let pickerTrigger = null
const scenes = [
  { id: 'layout', title: '摆放直播', heading: '选个布局，把直播拖进来。', description: '选择布局预设，再将关注栏里的直播拖入格子。喜欢的直播，各有位置。' },
  { id: 'ratio', title: '调整比例', heading: '把更多空间，留给主画面。', description: '拖动主画面与小画面之间的边框，按观看重点分配空间。' },
  { id: 'sound', title: '控制声音', heading: '多路同屏，只听想听的声音。', description: '每路直播独立调节音量与静音，其他画面继续保持原来的声音设置。' }
]
const scene = ref('layout')
const sceneInfo = computed(() => scenes.find(item => item.id === scene.value))
const manual = ref(false)
const held = ref(false)
const reduced = ref(false)
const tourTime = ref(0)
const tourFrame = ref(viewingTourFrame('layout', 0))
const ratio = ref(62)
const volumes = ref(Array(16).fill(40))
const muted = ref(Array(16).fill(false))
const cursor = ref({ x: 0, y: 0 })
let tourVisible = false, tourObserver, motion, tourRaf = 0, previousTime = 0, resizing = false
const columns = computed(() => ['corner', 'main2'].includes(presetId.value)
  ? `${ratio.value / 2}fr ${ratio.value / 2}fr ${100 - ratio.value}fr`
  : `repeat(${preset.value.cols}, minmax(0, 1fr))`)
function cardStyle(card) {
  return { backgroundImage: `url(${withBase('/wall/' + card.image)})`, backgroundPosition: 'center' }
}
function stopTour() { cancelAnimationFrame(tourRaf); tourRaf = 0; previousTime = 0 }
function takeControl() { manual.value = true; held.value = false; stopTour() }
function setRatio(value) { ratio.value = Math.max(40, Math.min(82, Number(value))) }
function setVolume(index, value) { volumes.value[index] = Math.max(0, Math.min(100, Number(value))) }
function toggleMute(index) { muted.value[index] = !muted.value[index] }
function resizeDown(event) { takeControl(); resizing = true; event.currentTarget.setPointerCapture?.(event.pointerId); resizeMove(event) }
function resizeMove(event) {
  if (!resizing) return
  const rect = root.value.querySelector('.mini-wall').getBoundingClientRect()
  setRatio((event.clientX - rect.left) / rect.width * 100)
}
function resizeUp(event) { resizing = false; event.currentTarget.releasePointerCapture?.(event.pointerId) }
function anchorPoint(name) {
  const surface = root.value?.querySelector('.software-miniature')
  if (!surface) return { x: 0, y: 0 }
  const box = surface.getBoundingClientRect()
  const selectors = { preset: '.mini-actions button', choice: '[data-tour-preset="2x2"]',
    'card-a': '[data-tour-card="a"]', 'card-b': '[data-tour-card="b"]', 'card-c': '[data-tour-card="c"]',
    'slot-0': '[data-mini-slot="0"]', 'slot-1': '[data-mini-slot="1"]', 'slot-2': '[data-mini-slot="2"]',
    'mute-1': '[data-tour-mute="1"]' }
  let rect = surface.querySelector(selectors[name] || (name.startsWith('divider') ? '.mini-wall' : '[data-tour-volume="0"]'))?.getBoundingClientRect()
  if (!rect?.width) return { x: box.width * .5, y: box.height * .5 }
  let fraction = .5
  if (name.startsWith('divider')) fraction = name === 'divider-large' ? .78 : name === 'divider-small' ? .55 : .62
  if (name.startsWith('volume')) fraction = name === 'volume-low' ? .15 : .4
  return { x: rect.left - box.left + rect.width * fraction, y: rect.top - box.top + rect.height * .5 }
}
function positionCursor() {
  const frame = tourFrame.value, from = anchorPoint(frame.from), to = anchorPoint(frame.to)
  cursor.value = { x: from.x + (to.x - from.x) * frame.progress, y: from.y + (to.y - from.y) * frame.progress }
}
function seekTour(time) {
  tourTime.value = Math.max(0, Math.min(TOUR_DURATION, Number(time)))
  const frame = viewingTourFrame(scene.value, tourTime.value)
  tourFrame.value = frame
  presetId.value = frame.preset
  pickerOpen.value = frame.picker
  slots.value = Array.from({ length: 16 }, (_, i) => i < frame.filled ? cards[i % cards.length] : null)
  ratio.value = frame.ratio
  volumes.value = Array(16).fill(40); volumes.value[0] = frame.volume
  muted.value = Array(16).fill(false); muted.value[1] = frame.muted
  status.value = frame.caption
  nextTick(positionCursor)
}
function tickTour(now) {
  tourRaf = 0
  if (!tourVisible || manual.value || held.value || reduced.value || document.hidden) { previousTime = 0; return }
  const elapsed = previousTime ? Math.min(100, now - previousTime) : 0
  previousTime = now
  if (tourTime.value + elapsed >= TOUR_DURATION) {
    scene.value = scenes[(scenes.findIndex(item => item.id === scene.value) + 1) % scenes.length].id
    seekTour(0)
  } else seekTour(tourTime.value + elapsed)
  tourRaf = requestAnimationFrame(tickTour)
}
function syncTour() {
  stopTour()
  if (tourVisible && !manual.value && !held.value && !reduced.value && !document.hidden) tourRaf = requestAnimationFrame(tickTour)
}
function selectScene(id) {
  if (!scenes.some(item => item.id === id)) return
  cancelCard(); pickerTrigger = null
  scene.value = id; manual.value = reduced.value; held.value = false
  seekTour(reduced.value ? 10500 : 0); syncTour()
}
function replayTour() { manual.value = reduced.value; held.value = false; seekTour(reduced.value ? 10500 : 0); syncTour() }
function scrubTour(event) { stopTour(); held.value = true; manual.value = reduced.value; seekTour(event.target.value) }
function motionChanged() {
  reduced.value = motion.matches
  if (reduced.value) { takeControl(); seekTour(10500) }
  syncTour()
}
onMounted(() => {
  motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionChanged(); motion.addEventListener('change', motionChanged)
  if ('IntersectionObserver' in window) {
    tourObserver = new IntersectionObserver(entries => { tourVisible = entries[0].isIntersecting; syncTour() }, { threshold: .25 })
    tourObserver.observe(root.value)
  } else { manual.value = true; seekTour(10500) }
  document.addEventListener('visibilitychange', syncTour)
})
onUnmounted(() => { stopTour(); tourObserver?.disconnect(); motion?.removeEventListener('change', motionChanged); document.removeEventListener('visibilitychange', syncTour) })
function cellStyle(cell) { return { gridArea: `${cell[0] + 1} / ${cell[1] + 1} / span ${cell[2]} / span ${cell[3]}` } }
function openPicker(event) {
  pickerTrigger = event.currentTarget
  cancelCard()
  pickerOpen.value = true
  nextTick(() => root.value?.querySelector('.preset-back')?.focus())
}
function closePicker() {
  if (!pickerOpen.value) return
  pickerOpen.value = false
  nextTick(() => pickerTrigger?.focus())
}
function selectCard(id) { selected.value = id; status.value = `已选择${cards.find(card => card.id === id).name}，点击目标画面放入。` }
function placeCard(index, id = selected.value) {
  const card = cards.find(item => item.id === id)
  if (!card || index < 0 || index >= preset.value.cells.length) return
  slots.value[index] = card
  selected.value = null
  landed.value = index
  placement.value++
  status.value = `${card.name}已放入第 ${index + 1} 个画面。`
}
function clearSlot(index) { slots.value[index] = null; status.value = `第 ${index + 1} 个画面已清空。` }
function cancelCard() { cardStart = null; dragged.value = null; over.value = -1 }
function changePreset(id) {
  if (!presets.some(item => item.id === id)) return
  cancelCard()
  presetId.value = id
  closePicker()
  status.value = `已切换为${preset.value.name}，已放入的卡片保留。`
}
function onCardDown(event, card) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  cardStart = { card, x: event.clientX, y: event.clientY }
  event.currentTarget.setPointerCapture?.(event.pointerId)
}
function onCardMove(event) {
  if (!cardStart) return
  if (!dragged.value && Math.hypot(event.clientX - cardStart.x, event.clientY - cardStart.y) < 6) return
  dragged.value = cardStart.card
  point.value = { x: event.clientX, y: event.clientY }
  const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-mini-slot]')
  over.value = target && root.value?.contains(target) ? Number(target.dataset.miniSlot) : -1
}
function onCardUp(event) {
  if (!cardStart) return
  if (dragged.value) { onCardMove(event); if (over.value >= 0) placeCard(over.value, dragged.value.id) }
  else selectCard(cardStart.card.id)
  cancelCard()
  event.currentTarget.releasePointerCapture?.(event.pointerId)
}
</script>

<template>
  <div ref="root" class="feature-demo" :data-scene="scene" :data-tour-time="Math.round(tourTime)" :data-manual="manual" @keydown.esc="cancelCard(); closePicker()">
    <div class="demo-selector" data-reveal>
      <div class="demo-scenes" role="group" aria-label="观看体验演示">
        <button v-for="(item, i) in scenes" :key="item.id" class="demo-scene" :aria-pressed="scene === item.id" @click="selectScene(item.id)"><span>0{{ i + 1 }}</span>{{ item.title }}<span aria-hidden="true">↗</span></button>
      </div>
      <div class="demo-description"><h3>{{ sceneInfo.heading }}</h3><p>{{ sceneInfo.description }}</p><p class="demo-tip">看光标演示，也可以亲手试试。预览只演示操作，不播放直播声音。</p></div>
    </div>
    <div class="demo-preview" data-reveal data-reveal-delay="120">
      <div class="demo-toolbar"><span>DD 监控室 CE · 可操作缩略预览</span><div><button v-if="(manual || held) && !reduced" @click="replayTour">观看演示</button><button v-if="!manual" @click="takeControl">自己试试</button><button @click="replayTour">重播</button></div></div>
      <div class="software-miniature" :class="{ 'is-light': light }" role="group" aria-label="软件外观缩略预览" @pointerdown.capture="takeControl" @keydown.capture="takeControl">
        <aside v-show="!pickerOpen" class="mini-sidebar">
          <div class="mini-brand" aria-hidden="true">DD监控室CE <span>«</span></div>
          <div class="mini-tools"><span aria-hidden="true">排序</span><button class="mini-theme" :aria-label="light ? '预览切换为深色' : '预览切换为浅色'" :aria-pressed="light" @click="light = !light"><span aria-hidden="true">◐</span></button><span aria-hidden="true">↻</span></div>
          <div class="mini-folder">关注 · {{ cards.length }}</div>
          <button v-for="card in cards" :key="card.id" class="mini-follow" :data-tour-card="card.id" :class="{ 'is-selected': selected === card.id, 'is-dragging': dragged?.id === card.id }" :aria-label="'选择' + card.name" :aria-pressed="selected === card.id"
            @pointerdown="onCardDown($event, card)" @pointermove="onCardMove" @pointerup="onCardUp" @pointercancel="cancelCard" @click="$event.detail === 0 && selectCard(card.id)">
            <span class="mini-card-image" :style="cardStyle(card)"></span><span>{{ card.name }}</span>
          </button>
          <div class="mini-actions"><button :aria-expanded="pickerOpen" aria-controls="mini-layout-presets" @click="openPicker($event)">布局预设</button></div>
        </aside>
        <div v-show="!pickerOpen" class="mini-wall" :style="{ gridTemplateColumns: columns, gridTemplateRows: `repeat(${preset.rows}, minmax(0, 1fr))` }">
          <div v-for="(cell, i) in preset.cells" :key="i" class="mini-tile" :class="{ 'is-over': over === i }" :style="cellStyle(cell)" :data-mini-slot="i">
            <button class="tile-target" :style="slots[i] ? cardStyle(slots[i]) : undefined" :aria-label="`第 ${i + 1} 个画面，${slots[i]?.name || '空位'}${selected ? '，放入所选卡片' : ''}`" @click="placeCard(i)">
              <span class="mini-badge" aria-hidden="true"><b>{{ slots[i] ? '演示' : '空位' }}</b> · {{ slots[i]?.name || '画面 ' + (i + 1) }}</span>
              <span v-if="!slots[i]" class="mini-video-label" aria-hidden="true">拖入直播</span>
            </button>
            <button v-if="slots[i]" class="mini-clear" :aria-label="`清空第 ${i + 1} 个画面`" @click="clearSlot(i)">×</button>
            <div class="mini-controls"><span aria-hidden="true">Ⅱ</span><button :data-tour-mute="i" :aria-label="`${muted[i] ? '取消静音' : '静音'}第 ${i + 1} 个画面`" :aria-pressed="muted[i]" @click="toggleMute(i)">{{ muted[i] ? '×' : '♪' }}</button><input type="range" :data-tour-volume="i" :aria-label="`第 ${i + 1} 个画面音量`" min="0" max="100" :value="volumes[i]" @input="setVolume(i, $event.target.value)" /><span class="mini-volume">{{ muted[i] ? '静音' : volumes[i] }}</span><span aria-hidden="true">⛶</span></div>
            <span v-if="landed === i" :key="placement" class="drop-flash" aria-hidden="true" @animationend="landed = -1"></span>
          </div>
          <div v-if="['corner', 'main2'].includes(presetId)" class="mini-divider" role="separator" aria-label="主画面宽度" aria-orientation="vertical" tabindex="0" aria-valuemin="40" aria-valuemax="82" :aria-valuenow="Math.round(ratio)" :style="{ left: ratio + '%' }" @pointerdown.stop="resizeDown" @pointermove="resizeMove" @pointerup="resizeUp" @pointercancel="resizeUp" @keydown.left.prevent="setRatio(ratio - 2)" @keydown.right.prevent="setRatio(ratio + 2)"><span></span></div>
        </div>
        <section v-show="pickerOpen" id="mini-layout-presets" class="preset-picker" aria-label="布局预设页面">
          <div class="preset-header"><button class="preset-back" @click="closePicker">← 返回预览</button><h4>布局预设</h4><span>{{ preset.name }}</span></div>
          <div class="preset-grid" role="group" aria-label="布局预设选项"><button v-for="item in presets" :key="item.id" :data-tour-preset="item.id" :aria-pressed="presetId === item.id" @click="changePreset(item.id)">
            <svg viewBox="0 0 90 60" aria-hidden="true"><rect v-for="(cell, j) in item.cells" :key="j" :x="cell[1] * 90 / item.cols + 2" :y="cell[0] * 60 / item.rows + 2" :width="cell[3] * 90 / item.cols - 4" :height="cell[2] * 60 / item.rows - 4" rx="2" /></svg><span>{{ item.name }}</span>
          </button></div>
        </section>
        <div v-if="!manual && !reduced" class="demo-cursor" :class="{ 'is-pressed': tourFrame.pressed }" :style="{ transform: `translate3d(${cursor.x}px, ${cursor.y}px, 0)` }" aria-hidden="true">
          <div v-if="tourFrame.ghost" class="demo-card-ghost" :style="cardStyle(cards.find(card => card.id === tourFrame.ghost))"></div>
          <svg viewBox="0 0 28 36" width="25" height="32"><path d="M3 2v26l7-7 6 12 5-3-6-11h10Z" fill="white" stroke="#101820" stroke-width="2" stroke-linejoin="round" /></svg><i v-if="tourFrame.pressed"></i>
        </div>
      </div>
      <input class="demo-scrubber" type="range" aria-label="观看体验演示进度" min="0" :max="TOUR_DURATION" step="100" :value="tourTime" @input="scrubTour" />
      <p class="demo-status" role="status">{{ status }}</p>
    </div>
    <Teleport to="body"><div v-if="dragged" class="card-ghost" aria-hidden="true" :style="{ left: point.x + 'px', top: point.y + 'px', ...cardStyle(dragged) }">{{ dragged.name }}</div></Teleport>
  </div>
</template>

<style scoped>
.feature-demo { display: grid; grid-template-columns: minmax(220px, .65fr) minmax(0, 1.35fr); gap: 36px; align-items: center; }
.demo-scenes { display: grid; gap: 8px; }
.demo-scene { display: flex; align-items: center; gap: 16px; width: 100%; min-height: 56px; padding: 14px 16px; text-align: left; border: 1px solid var(--vp-c-divider); border-radius: 8px; color: var(--vp-c-text-2); background: transparent; transition: background 180ms var(--ease-out), border-color 180ms var(--ease-out); }
.demo-scene[aria-pressed=true] { border-color: var(--vp-c-brand-1); color: var(--vp-c-brand-1); background: var(--vp-c-brand-soft); }
.demo-scene span:first-child { font-size: 12px; opacity: .65; }
.demo-scene span:last-child { margin-left: auto; }
.demo-scene:focus-visible, .mini-divider:focus-visible { outline: 2px solid var(--vp-c-brand-1); outline-offset: 3px; }
.demo-description { margin-top: 26px; }
.demo-description h3 { font-size: 22px; font-weight: 600; margin: 0 0 12px; }
.demo-description p { font-size: 15px; line-height: 1.85; color: var(--vp-c-text-2); }
.demo-description .demo-tip { font-size: 13px; margin-top: 16px; }
.demo-preview { min-width: 0; padding: 18px; border: 1px solid var(--vp-c-divider); border-radius: 12px; background: var(--vp-c-bg-soft); }
.demo-toolbar { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 14px; color: var(--vp-c-text-2); font-size: 12px; }
.demo-toolbar > div { display: flex; gap: 12px; white-space: nowrap; }
.demo-toolbar button { color: var(--vp-c-brand-1); }
.software-miniature { --mini-bg: #222428; --mini-side: #1d2023; --mini-card: #303338; --mini-text: #f1f2f5; --mini-muted: #aeb6c1; --mini-border: #414851; --mini-overlay: #17202b; display: grid; grid-template-columns: 22% minmax(0,1fr); gap: 7px; padding: 7px; position: relative; aspect-ratio: 800 / 520; border-radius: 9px; background: var(--mini-bg); color: var(--mini-text); line-height: 1.35; }
.software-miniature.is-light { --mini-bg: #f4f6f9; --mini-side: #ebeef3; --mini-card: #fff; --mini-text: #202630; --mini-muted: #4a5565; --mini-border: #d7dde7; --mini-overlay: #f7f9fc; }
.mini-sidebar { display: flex; flex-direction: column; gap: 5px; min-width: 0; min-height: 0; padding: 5px; border-radius: 5px; background: var(--mini-side); font-size: clamp(7px,.75vw,10px); }
.mini-brand { display: flex; justify-content: space-between; font-weight: 600; white-space: nowrap; }
.mini-tools { display: grid; grid-template-columns: 1fr 22px 22px; align-items: center; gap: 2px; color: var(--mini-muted); }
.mini-tools > span:last-child { text-align: center; }
.mini-theme { display: grid; place-items: center; width: 22px; height: 22px; padding: 0; border-radius: 50%; font-size: 15px; line-height: 1; color: var(--mini-text); background: var(--mini-card); }
.mini-folder { padding: 4px 2px; border-bottom: 1px solid var(--mini-border); }
.mini-follow { flex: 1; position: relative; display: flex; flex-direction: column; justify-content: end; min-height: 0; padding: 4px; gap: 3px; border: 1px solid transparent; border-radius: 5px; background: var(--mini-card); color: var(--mini-text); touch-action: none; cursor: grab; user-select: none; text-align: left; }
.mini-card-image { flex: 1; width: 100%; min-height: 0; border-radius: 3px; background-size: cover; }
.mini-follow { font-size: inherit; line-height: 1.35; }
.mini-follow > span:last-child { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.mini-follow.is-selected { border-color: #00a1d6; }
.mini-follow.is-dragging { opacity: .45; }
.mini-actions { margin-top: 3px; }
.mini-actions button { width: 100%; min-height: 24px; border-radius: 4px; background: var(--mini-card); font-size: inherit; }
.mini-wall { position: relative; display: grid; gap: 6px; min-width: 0; }
.mini-tile { position: relative; min-width: 0; min-height: 0; border-radius: 5px; background: #101114; border: 1px solid var(--mini-border); }
.tile-target { position: absolute; inset: 0; display: grid; place-items: center; width: 100%; height: 100%; border-radius: 4px; background-size: cover; overflow: hidden; }
.mini-badge { position: absolute; top: 4px; left: 4px; max-width: calc(100% - 8px); overflow: hidden; white-space: nowrap; padding: 2px 4px; border-radius: 3px; font-size: clamp(6px,.65vw,9px); background: var(--mini-overlay); color: var(--mini-text); }
.mini-badge b { color: #fb7299; }
.is-light .mini-badge b { color: #b93665; }
.mini-video-label { color: #78818b; font-size: clamp(7px,.8vw,11px); }
.mini-controls { position: absolute; inset: auto 0 0; display: flex; align-items: center; gap: 5px; min-height: 24px; padding: 3px 4px; background: var(--mini-overlay); color: var(--mini-muted); font-size: clamp(6px,.7vw,10px); border-radius: 0 0 4px 4px; }
.mini-controls button { display: grid; place-items: center; min-width: 18px; min-height: 18px; color: inherit; border-radius: 3px; }
.mini-controls button[aria-pressed=true] { color: #fb7299; background: #fb729919; }
.mini-controls input { width: 100%; min-width: 0; flex: 1; height: 18px; accent-color: #00a1d6; cursor: ew-resize; }
.mini-volume { min-width: 18px; text-align: right; }
.mini-divider { position: absolute; top: 0; bottom: 0; width: 14px; transform: translateX(-50%); z-index: 3; cursor: col-resize; touch-action: none; display: grid; place-items: center; }
.mini-divider span { width: 3px; height: 36px; border-radius: 3px; background: #00b6e6; box-shadow: 0 0 0 3px #0b1529bb; }
.demo-cursor { position: absolute; top: 0; left: 0; z-index: 8; width: 25px; height: 32px; pointer-events: none; filter: drop-shadow(0 2px 3px #0008); }
.demo-cursor svg { position: relative; z-index: 1; }
.demo-cursor.is-pressed svg { transform: scale(.9); transform-origin: 3px 3px; }
.demo-cursor i { position: absolute; left: -9px; top: -9px; width: 24px; height: 24px; border: 2px solid #00c8ff; border-radius: 50%; background: #00c8ff30; }
.demo-card-ghost { position: absolute; left: -40px; top: -24px; width: 90px; height: 54px; border: 2px solid #00c8ff; border-radius: 5px; background-size: cover; opacity: .9; }
.demo-scrubber { display: block; width: 100%; height: 16px; margin-top: 12px; accent-color: var(--vp-c-brand-1); cursor: ew-resize; }
.mini-clear { position: absolute; right: 2px; top: 2px; width: 22px; height: 22px; color: #fff; background: #101418c9; border-radius: 50%; }
.mini-tile.is-over { outline: 2px solid #00a1d6; outline-offset: 1px; }
.preset-picker { position: absolute; inset: 7px; overflow: auto; border-radius: 6px; padding: 12px; background: var(--mini-side); color: var(--mini-text); }
.preset-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.preset-header h4 { font-size: 14px; font-weight: 600; }
.preset-header > span { margin-left: auto; font-size: 10px; color: var(--mini-muted); }
.preset-back { min-height: 32px; padding: 4px 8px; border: 1px solid var(--mini-border); border-radius: 5px; font-size: 11px; }
.preset-grid { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 6px; padding: 4px 10px 10px; }
.preset-grid button { min-width: 0; padding: 7px; border: 1px solid var(--mini-border); border-radius: 6px; font-size: 10px; }
.preset-grid button[aria-pressed=true] { border-color: var(--vp-c-brand-1); color: var(--vp-c-brand-1); }
.preset-grid svg { display: block; width: 100%; height: 44px; margin-bottom: 5px; fill: currentColor; opacity: .7; }
.demo-status { color: var(--vp-c-text-2); font-size: 12px; line-height: 1.7; }
.demo-status { color: var(--vp-c-text-2); font-size: 12px; line-height: 1.7; }
.demo-status { min-height: 40px; margin: 12px 0 0; }
.card-ghost { position: fixed; z-index: 100; width: 120px; height: 76px; padding: 7px; display: flex; align-items: end; border: 1px solid #00a1d6; border-radius: 7px; background-size: cover; color: white; text-shadow: 0 1px 4px black; transform: translate(-50%,-50%) rotate(-3deg); box-shadow: 0 16px 40px #0005; pointer-events: none; }
.drop-flash { position: absolute; inset: -2px; border: 2px solid #00a1d6; border-radius: 6px; pointer-events: none; animation: drop-land 360ms var(--ease-out) both; }
@keyframes drop-land { from { opacity: 1; transform: scale(.96); } to { opacity: 0; transform: scale(1.04); } }
@media (hover:hover) and (pointer:fine) { .mini-follow:hover { border-color: #00a1d6; } .preset-grid button:hover { background: var(--mini-card); } }
@media (max-width:900px) { .feature-demo { grid-template-columns: 1fr; gap: 20px; } .demo-scenes { grid-template-columns: repeat(3,minmax(0,1fr)); } .demo-scene { min-height: 48px; padding: 10px; gap: 8px; font-size: 12px; } .demo-scene span:last-child { display:none; } .demo-description { margin-top: 18px; } }
@media (max-width:480px) { .demo-preview { padding: 12px; } .software-miniature { gap: 4px; padding: 4px; } .mini-wall { gap: 4px; } .mini-sidebar { padding: 3px; gap: 3px; } .mini-controls { gap: 3px; padding: 3px; } .mini-badge { top: 2px; left: 2px; padding: 1px 2px; } .mini-brand { font-size: 6px; } .mini-tools { grid-template-columns: 1fr 18px 14px; } .mini-theme { width:18px; height:18px; font-size:13px; } .preset-grid { grid-template-columns:repeat(2,minmax(0,1fr)); padding:0; } .preset-picker { inset:4px; padding:8px; } .preset-header { gap:6px; margin-bottom:8px; flex-wrap:wrap; } .preset-header > span { display:none; } }
@media (prefers-reduced-motion:reduce) { .drop-flash { display:none; animation:none; } .demo-cursor { display:none; } .card-ghost { transform:translate(-50%,-50%); } }
@media (max-width:480px) { .software-miniature { width:100%; min-width:0; min-height:220px; } }
@media (max-width:480px) { .mini-controls > span:first-child, .mini-controls > span:last-child { display:none; } .mini-controls { gap:2px; } .mini-controls button { min-width:14px; } .mini-volume { min-width:12px; } }
</style>
