// 继续观看栏纯逻辑（进度条宽度 / 「全部最近播放」记忆），node 单测。
import { fmtRemaining } from './format.js'
import { fmtTime } from './playerLabels.js'
import { isPlaybackComplete } from './progress.js'

export const RECENT_ALL_KEY = 'jzmedia.recentAll'

export function canResumeRecent(item) {
  if (!item?.progress?.version_id || item.missing) return false
  if (typeof item.can_resume === 'boolean') return item.can_resume
  return !item.watched && !isPlaybackComplete(item.progress.position, item.progress.duration)
}

export function recentProgressLabel(item) {
  if (item.missing) return '文件缺失'
  if (item.watched || isPlaybackComplete(item.progress?.position, item.progress?.duration)) return '已看完'
  if (item.progress?.duration > 0) return fmtRemaining(item.progress.remaining_sec)
  return item.progress?.position > 0 ? `已观看 ${fmtTime(item.progress.position)}` : ''
}

// 进度条宽度：percent ∈ [0,1] → CSS 百分比；至少 min% 让细进度也可见
export function progressWidth(p, min = 2) {
  const pct = Number(p && p.percent)
  if (!Number.isFinite(pct) || pct <= 0) return min + '%'
  return Math.max(min, Math.min(100, pct * 100)).toFixed(1) + '%'
}

export function loadRecentAll(storage) {
  try { return storage.getItem(RECENT_ALL_KEY) === '1' } catch (e) { return false }
}

export function saveRecentAll(storage, on) {
  try { storage.setItem(RECENT_ALL_KEY, on ? '1' : '0') } catch (e) { /* 忽略 */ }
}

// 横向滚动状态：是否还能向左/向右滚（容差防亚像素抖动）
export function canScroll({ scrollLeft = 0, clientWidth = 0, scrollWidth = 0 } = {}, eps = 2) {
  return {
    left: scrollLeft > eps,
    right: scrollLeft + clientWidth < scrollWidth - eps,
  }
}

// 左右箭头的单步滚动量：一屏的 80%，至少一张卡
export function scrollStep(clientWidth = 0, min = 160) {
  return Math.max(min, Math.round((Number(clientWidth) || 0) * 0.8))
}
