// 自绘文本字幕（VTT）的外观与定位计算：无 DOM 依赖的纯函数，便于单测。
// 位置策略对齐 mpv `sub-use-margins`（默认把纯文本字幕放进黑边）与 Kodi「Bottom of screen」。
export const SUB_FONT_RATIO = [0.040, 0.048, 0.058]   // 小/中/大 × 画面高
export const SUB_FONT_MIN = 15
export const SUB_FONT_MAX = 46
export const SUB_POS_VALUES = ['auto', 'inside', 'outside']

export function normalizeSubStyle(d) {
  const o = (d && typeof d === 'object') ? d : {}
  return {
    bg: [0, 1, 2].includes(Number(o.bg)) ? Number(o.bg) : 0,
    outline: [0, 1, 2].includes(Number(o.outline)) ? Number(o.outline) : 1,
    pos: SUB_POS_VALUES.includes(o.pos) ? o.pos : 'auto',
    size: [1, 2, 3].includes(Number(o.size)) ? Number(o.size) : 2,
  }
}

export function subFontPx(pictureH, size) {
  const i = Math.min(3, Math.max(1, Number(size) || 2)) - 1
  const base = (Number(pictureH) || 0) * SUB_FONT_RATIO[i]
  return Math.max(SUB_FONT_MIN, Math.min(SUB_FONT_MAX, Math.round(base)))
}

// 锚定决策：auto=黑边容得下一行（字号+4px）就放黑边，否则画面内底部；手动强制
export function pickSubAnchor(barBottom, fontPx, pos) {
  if (pos === 'inside') return 'inside'
  if (pos === 'outside') return 'outside'
  return (Number(barBottom) || 0) >= (Number(fontPx) || 0) + 4 ? 'outside' : 'inside'
}

// 黑边模式底部内边距：随黑边高度自适应（3-16px），避免贴屏幕边缘
export function subBarPad(barBottom) {
  return Math.min(16, Math.max(3, Math.round((Number(barBottom) || 0) * 0.22)))
}

// 画面内模式底部内边距：画面高的 4.2%
export function subInnerPad(pictureH) {
  return Math.max(6, Math.round((Number(pictureH) || 0) * 0.042))
}
