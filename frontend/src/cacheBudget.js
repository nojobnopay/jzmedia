export const GIB = 1024 ** 3

// Keep the byte calculation exact, including a single byte above a 0.1 GB boundary.
export function reservedTenths(baseBytes) {
  const bytes = BigInt(baseBytes), gib = BigInt(GIB)
  return Number((10n * bytes + (bytes > 10n * gib ? bytes : 10n * gib) + gib - 1n) / gib)
}

export function capacityError(value, minimum) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '请输入容量数值'
  if (value < minimum || value > 102400) return `容量须在 ${minimum}～102400 GB 之间`
  if (Math.abs(value * 10 - Math.round(value * 10)) > 1e-6) return '容量最多保留一位小数'
  return ''
}

export function cacheBudget(limits, usage) {
  const parts = usage?.categories
  const available = usage?.ok === true && ['database', 'posters', 'other', 'transcode', 'previews'].every(key =>
    Number.isSafeInteger(parts?.[key]) && parts[key] >= 0)
  const unavailable = { available: false, editable: false, valid: false, segments: [] }
  if (!available) return { ...unavailable, error: '暂时无法统计基础数据占用，请刷新占用后再保存。' }
  const baseBytes = parts.database + parts.posters + parts.other
  if (!Number.isSafeInteger(baseBytes)) return { ...unavailable, error: '基础数据占用无法可靠计算，请刷新占用后再保存。' }
  const reserve = reservedTenths(baseBytes)
  const result = { available: true, baseBytes, reservedGb: reserve / 10, minimumTotalGb: (reserve + 2) / 10,
    segments: [], editable: false, valid: false, error: '' }
  if (limits.transcode_cache_gb === 0) return { ...result, error: '播放缓存未限制容量（仅按过期时间清理），请设置正的播放缓存额度后保存。' }
  if (capacityError(limits.data_warning_gb, 1) || capacityError(limits.transcode_cache_gb, 0.1)
    || capacityError(limits.preview_cache_gb, 0.1)) return { ...result, error: '请填写有效的总预算和两项缓存额度。' }
  const total = Math.round(limits.data_warning_gb * 10)
  const playback = Math.round(limits.transcode_cache_gb * 10), previews = Math.round(limits.preview_cache_gb * 10)
  const allocated = reserve + playback + previews, excess = Math.max(0, allocated - total)
  const free = Math.max(0, total - allocated), scale = Math.max(total, allocated)
  let error = ''
  if (excess) {
    const excessText = `超出预算 ${(excess / 10).toFixed(1)} GB`
    if (reserve + 2 > 1024000) error = `${excessText}；基础数据预留与两项最小缓存至少需要 ${result.minimumTotalGb.toFixed(1)} GB 总预算，已超过可配置上限 102400 GB，无法仅通过增加总预算解决，请先减少基础数据占用。`
    else if (reserve + 2 > total) error = `${excessText}；基础数据预留与两项最小缓存至少需要 ${result.minimumTotalGb.toFixed(1)} GB 总预算，仅调低缓存额度仍不足，请增加总预算。`
    else error = `${excessText}，请增加总预算或调低缓存额度。`
  }
  const segments = [
    { key: 'base', label: '基础数据预留', tenths: reserve },
    { key: 'transcode', label: '播放缓存', tenths: playback },
    { key: 'previews', label: '进度缩略图', tenths: previews },
    { key: 'free', label: '未分配余量', tenths: free },
  ].map(part => ({ ...part, gb: part.tenths / 10, percent: part.tenths / scale * 100 }))
  return { ...result, segments, allocatedGb: allocated / 10, freeGb: free / 10, excessGb: excess / 10,
    scaleGb: scale / 10, editable: true, valid: excess === 0, error }
}

// Each handle edits one quota independently. Temporary over-allocation is valid
// editing state; only saving requires both quotas to fit within the total budget.
export function allocationRange(limits, usage, part) {
  const budget = cacheBudget(limits, usage)
  if (!budget.editable) return null
  if (part === 'transcode') return { min: 0.1, max: 102400, value: Math.round(limits.transcode_cache_gb * 10) / 10 }
  if (part === 'previews') return { min: 0.1, max: 102400, value: Math.round(limits.preview_cache_gb * 10) / 10 }
  return null
}

export function moveAllocationBoundary(limits, usage, part, valueGb) {
  const range = allocationRange(limits, usage, part)
  if (!range || !Number.isFinite(valueGb)) return { ...limits }
  const value = Math.max(1, Math.min(Math.round(range.max * 10), Math.round(valueGb * 10)))
  const key = part === 'transcode' ? 'transcode_cache_gb' : 'preview_cache_gb'
  return { ...limits, [key]: value / 10 }
}
