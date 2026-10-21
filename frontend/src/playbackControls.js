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

// 画内控件隐藏时首次点击只点亮控件、不暂停（窗口/全屏相同）。
// overlayAtPointerDown 取按下瞬间的显隐（pointerdown 先于 click 点亮控件，不能用 click 时的状态判断）。
export function surfaceClickRevealsOnly(overlayAtPointerDown) {
  return !overlayAtPointerDown
}

// Centre the button itself, reserving picture tools and subtitle space on phones.
// Tiny picture areas retain the bottom play control instead of overlapping actions.
export function pauseButtonPlacement(height, { mobile = false, fullscreen = false, controlsHeight = 0 } = {}) {
  height = Number(height)
  if (!Number.isFinite(height) || height <= 0) return { visible: false, size: 0, center: 0, showHint: false }
  const desired = mobile ? 60 : 76
  const top = mobile && !fullscreen ? 64 : 0
  const bottom = mobile && !fullscreen ? 32 : 16
  const center = mobile && !fullscreen ? (height + top - bottom) / 2 : height / 2
  const limit = fullscreen ? Math.min(height - 32, 2 * (height / 2 - controlsHeight - 8)) : height - top - bottom
  const size = Math.min(desired, limit)
  const hintLimit = fullscreen ? height - controlsHeight - 8 : height - bottom
  return { visible: size >= 44, size: Math.max(0, size), center,
    showHint: center + size / 2 + 22 <= hintLimit }
}

// undefined is an ordinary/legacy preview; null rejects an invalid new contract.
export function previewTailCoverage(value, strict = false) {
  if (!value) return undefined
  const bounded = value.available_count !== undefined || value.coverage_end !== undefined || value.warning?.code === 'confirmed_tail_damage'
  if (!bounded && [undefined, 'complete'].includes(value.completion)) return undefined
  if (value.completion !== 'known_tail_missing' || value.warning?.code !== 'confirmed_tail_damage') return null
  const { duration, interval, count, available_count: availableCount, coverage_end: coverageEnd } = value
  if (!Number.isFinite(duration) || !(duration > 0 && duration <= 86400)
      || !Number.isInteger(interval) || interval < 5 || interval > 60
      || !Number.isInteger(availableCount) || availableCount < 1
      || !Number.isFinite(coverageEnd) || coverageEnd !== availableCount * interval || coverageEnd >= duration
      || (count !== undefined && (!Number.isInteger(count) || count !== Math.ceil(duration / interval) || availableCount >= count))) return null
  if (strict) {
    const { columns, rows, width, height, pages } = value
    if (value.state !== 'ready' || !Number.isInteger(count)
        || ![columns, rows, width, height].every(number => Number.isInteger(number) && number > 0)
        || !Array.isArray(pages) || pages.length !== Math.ceil(availableCount / (columns * rows))
        || !pages.every(page => typeof page === 'string' && !!page)) return null
  }
  return { availableCount, coverageEnd, duration, missingSeconds: duration - coverageEnd }
}

export function previewFrame(manifest, time) {
  if (!manifest?.pages?.length || !(manifest.interval > 0)) return null
  const coverage = previewTailCoverage(manifest, true)
  if (coverage === null || !Number.isFinite(time)) return null
  if (coverage && (time >= coverage.coverageEnd || Math.floor(time / manifest.interval) >= coverage.availableCount)) return null
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

// Sprite coordinates are bitmap pixels; the preview stays the same CSS size on
// every screen. Higher resolution sheets supply the extra pixels for HiDPI.
export function previewImageStyle(frame) {
  if (!frame) return {}
  const scale = 160 / frame.width
  return {
    width: '160px', height: frame.height * scale + 'px',
    backgroundImage: `url("${frame.url}")`,
    backgroundPosition: `-${frame.x * scale}px -${frame.y * scale}px`,
    backgroundSize: `${frame.sheetWidth * scale}px ${frame.sheetHeight * scale}px`,
  }
}
