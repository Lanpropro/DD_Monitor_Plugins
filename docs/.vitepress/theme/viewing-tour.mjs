export const TOUR_CHAPTERS = [
  { id: 'layout', start: 0, end: 22000 },
  { id: 'ratio', start: 22000, end: 40000 },
  { id: 'sound', start: 40000, end: 58000 }
]
export const TOUR_DURATION = TOUR_CHAPTERS.at(-1).end

const clamp = value => Math.min(1, Math.max(0, value))
const between = (time, start, end) => clamp((time - start) / (end - start))

// One clock drives both the virtual pointer and the resulting software state.
export function viewingTourFrame(scene, time) {
  const chapter = TOUR_CHAPTERS.find(item => item.id === scene)
  const localTime = Math.max(0, Math.min(chapter.end - chapter.start, time))
  const t = scene === 'layout' ? localTime : localTime / 1.5
  let points, frame
  if (scene === 'layout') {
    points = [[0, 'preset'], [900, 'preset'], [1800, 'choice'], [2800, 'choice']]
    let ghost = null, filled = t < 2800 ? 6 : 0, followIndex = 0, followProgress = 0
    for (let i = 0; i < 6; i++) {
      const pickup = 4200 + i * 2400, drop = pickup + 1400
      const card = String.fromCharCode(97 + i)
      points.push([pickup - 400, `card-${card}`], [pickup, `card-${card}`], [drop, `slot-${i}`], [drop + 300, `slot-${i}`])
      if (t >= pickup - 600) { followIndex = i; followProgress = between(t, pickup - 600, pickup - 400) }
      if (t >= pickup && t < drop) ghost = card
      if (t >= drop) filled++
    }
    points.push([22000, 'slot-5'])
    frame = { preset: 'corner', picker: t >= 1200 && t < 2800, filled, followIndex, followProgress,
      ratio: 62, volume: 40, muted: false, ghost,
      pressed: !!ghost || (t >= 900 && t < 1200) || (t >= 2500 && t < 2800),
      caption: t < 2800 ? '选择主画面 + 5 小布局。' : t < 17600 ? '把六位主播依次拖入主画面和五个小画面。' : '六位主播已全部就位，一个主画面，五个小画面。' }
  } else if (scene === 'ratio') {
    points = [[0, 'divider-start'], [1400, 'divider-start'], [5400, 'divider-large'],
      [7400, 'divider-large'], [10000, 'divider-small'], [12000, 'divider-small']]
    frame = { preset: 'corner', picker: false, filled: 6,
      ratio: t < 7400 ? 62 + 16 * between(t, 1400, 5400) : 78 - 23 * between(t, 7400, 10000),
      volume: 40, muted: false, ghost: null, pressed: (t >= 1400 && t < 5400) || (t >= 7400 && t < 10000),
      caption: t < 5400 ? '拖动边框，给主画面更多空间。' : t < 7400 ? '主画面放大，小画面继续留在旁边。' : '向回拖动，重新分配观看空间。' }
  } else {
    points = [[0, 'volume-start'], [1400, 'volume-start'], [4700, 'volume-low'],
      [5600, 'volume-low'], [7000, 'mute-1'], [8200, 'mute-1'], [12000, 'mute-1']]
    frame = { preset: 'corner', picker: false, filled: 6, ratio: 62,
      volume: Math.round(40 - 25 * between(t, 1400, 4700)), muted: t >= 7500,
      ghost: null, pressed: (t >= 1400 && t < 4700) || (t >= 7300 && t < 7800),
      caption: t < 5600 ? '单独调低主画面的音量。' : t < 7500 ? '其他画面的音量保持不变。' : '静音其中一路，保留想听的声音。' }
  }
  let index = points.findIndex((point, i) => i < points.length - 1 && t < points[i + 1][0])
  if (index < 0) index = points.length - 2
  const [start, from] = points[index], [end, to] = points[index + 1]
  return { ...frame, from, to, progress: between(t, start, end) }
}
