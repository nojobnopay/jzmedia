const gap = 10

// Bounds describe where the top-left corner of the projector can move.
export function placeAssistant(point, bounds, width = 396) {
  const { left, right, top, bottom, size } = bounds
  const before = point.x - left - gap
  const after = right - point.x - gap
  const span = bottom - top + size
  const horizontalSpace = Math.max(before, after)
  const short = span < 300
  if (horizontalSpace >= (short ? 220 : 300) && (right - left + size >= 560 || short)) {
    const toLeft = before >= after
    const panelWidth = Math.min(width, Math.max(before, after))
    // Use the same fraction on both shapes, so a change in the message
    // height keeps the pointer beside the character without a layout read.
    const fraction = bottom > top ? (point.y - top) / (bottom - top) : 0
    return {
      edge: toLeft ? 'right' : 'left',
      x: toLeft ? point.x - gap - panelWidth : point.x + size + gap,
      y: point.y + size * fraction,
      offset: -100 * fraction,
      width: panelWidth,
      maxHeight: Math.min(484, span),
      pointer: `calc(${fraction * 100}% + ${(0.5 - fraction) * size}px)`,
    }
  }
  const above = point.y - top - gap
  const below = bottom - point.y - gap
  const panelWidth = Math.min(width, right - left + size)
  const cx = point.x + size / 2
  const x = Math.min(right + size - panelWidth, Math.max(left, cx - panelWidth / 2))
  const toTop = above > below
  return {
    edge: toTop ? 'bottom' : 'top',
    x, y: toTop ? point.y - gap : point.y + size + gap,
    offset: toTop ? -100 : 0,
    width: panelWidth,
    maxHeight: Math.max(0, Math.min(612, Math.max(above, below))),
    pointer: (cx - x) + 'px',
  }
}
