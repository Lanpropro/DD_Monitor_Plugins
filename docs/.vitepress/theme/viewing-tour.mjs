export const TOUR_DURATION = 12000

const clamp = value => Math.min(1, Math.max(0, value))
const between = (time, start, end) => clamp((time - start) / (end - start))

// One clock drives both the virtual pointer and the resulting software state.
export function viewingTourFrame(scene, time) {
  const t = Math.max(0, Math.min(TOUR_DURATION, time))
  let points, frame
  if (scene === 'layout') {
    points = [[0, 'preset'], [900, 'preset'], [1500, 'choice'], [2000, 'choice'],
      [2700, 'card-a'], [3000, 'card-a'], [4400, 'slot-0'], [4900, 'card-b'],
      [5200, 'card-b'], [6600, 'slot-1'], [7100, 'card-c'], [7400, 'card-c'], [8800, 'slot-2'], [12000, 'slot-2']]
    const ghost = t >= 3000 && t < 4400 ? 'a' : t >= 5200 && t < 6600 ? 'b' : t >= 7400 && t < 8800 ? 'c' : null
    frame = { preset: t < 2200 ? 'corner' : '2x2', picker: t >= 1100 && t < 2200,
      filled: t < 2200 ? 6 : t < 4400 ? 0 : t < 6600 ? 1 : t < 8800 ? 2 : 3,
      ratio: 62, volume: 40, muted: false, ghost,
      pressed: !!ghost || (t >= 850 && t < 1100) || (t >= 1900 && t < 2200),
      caption: t < 2200 ? '选择四分布局。' : t < 8800 ? '把关注卡片依次拖入画面。' : '布局已就位，随时换一个观看组合。' }
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
