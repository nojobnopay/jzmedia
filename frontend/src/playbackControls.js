export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2]

export function normalizeRate(value) {
  const rate = Number(value)
  return PLAYBACK_RATES.includes(rate) ? rate : 1
}

export function applyPlaybackRate(video, value) {
  if (!video) return
  const rate = normalizeRate(value)
  video.defaultPlaybackRate = rate
  video.playbackRate = rate
  if ('preservesPitch' in video) video.preservesPitch = true
  else if ('webkitPreservesPitch' in video) video.webkitPreservesPitch = true
}

export function containsTime(ranges, time, margin = 0) {
  if (!ranges || !Number.isFinite(time) || time < 0) return false
  for (let i = 0; i < ranges.length; i++) {
    if (time >= ranges.start(i) && time < ranges.end(i) - margin) return true
  }
  return false
}

// 媒体元素范围是片内时间。增长型 HLS 只信当前可定位范围，不能用全片时长代替。
export function reusableSeekTime(video, target, mediaStart = 0) {
  const time = target - mediaStart
  if (containsTime(video?.buffered, time, 0.15) || containsTime(video?.seekable, time, 0.15)) return time
  return null
}

// 全屏控件隐藏时首次点击只点亮控件、不暂停；窗口态控件常显，不受影响。
// overlayAtPointerDown 取按下瞬间的显隐（pointerdown 先于 click 点亮控件，不能用 click 时的状态判断）。
export function surfaceClickRevealsOnly(isFull, overlayAtPointerDown) {
  return Boolean(isFull) && !overlayAtPointerDown
}

export function previewFrame(manifest, time) {  if (!manifest?.pages?.length || !(manifest.interval > 0)) return null
  const count = Number(manifest.count) || 0
  const index = Math.min(count - 1, Math.max(0, Math.floor(time / manifest.interval)))
  if (index < 0) return null
  const cells = manifest.columns * manifest.rows
  const page = manifest.pages[Math.floor(index / cells)]
  if (!page) return null // 生成中：不把上一页的图片冒充尚未生成的目标帧
  const cell = index % cells
  return { url: page, x: (cell % manifest.columns) * manifest.width,
    y: Math.floor(cell / manifest.columns) * manifest.height,
    width: manifest.width, height: manifest.height,
    sheetWidth: manifest.width * manifest.columns, sheetHeight: manifest.height * manifest.rows }
}
