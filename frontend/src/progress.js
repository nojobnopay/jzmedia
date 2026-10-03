// 断点存档取值（纯函数，可 node 单测）：seek 重开中必须存用户目标位置，
// 不能存旧时间轴的 currentTime/absPos（用户 2026-09：30:28 拖到 14:30 关闭后重开回到 30:28）。
// 返回 null = 无有效播放位置，不存档（防 0:00 覆盖既有断点）。

export function pickProgressPosition({ seekPending, seekPreview, absPos, currentTime }) {
  if (seekPending) return Number(seekPreview) || 0   // doSeek 先写 seekPreview 再置位，含拖到 0:00
  if (!Number.isFinite(currentTime) || currentTime <= 0) return null
  return absPos
}

// Keep this rule aligned with app/playback_completion.py and Android PlaybackModels.
export function isPlaybackComplete(position, duration) {
  if (!Number.isFinite(position) || !Number.isFinite(duration) || position < 0 || duration <= 0) return false
  const ratio = position / duration
  return ratio >= 0.95 || (ratio >= 0.80 && duration - position <= 300)
}
