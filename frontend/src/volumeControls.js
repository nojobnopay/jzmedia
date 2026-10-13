export function normalizeVolume(value) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : 1
}

// Keep the visual vertical direction independent of the browser's range key map.
export function volumeForKey(key, volume) {
  if (key === 'Home') return 0
  if (key === 'End') return 1
  const direction = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[key]
  if (!direction) return null
  return normalizeVolume(Math.round((normalizeVolume(volume) + direction * 0.05) * 100) / 100)
}

// A mouse click follows pointerenter, and Enter follows keyboard focus. Keep
// that first activation open; subsequent activations use the normal toggle.
export function createVolumePopoverState() {
  let temporary = false
  let pinned = false
  return {
    openTemporarily(open, source = 'hover') {
      if (!open) temporary = true
      return { event: 'open', source }
    },
    click(open) {
      if (temporary && !pinned) {
        temporary = false
        pinned = true
        return { event: 'open', source: 'activate' }
      }
      temporary = false
      pinned = !open
      return { event: 'toggle' }
    },
    releaseTemporary({ focused = false, dragging = false } = {}) {
      if (!temporary || pinned || focused || dragging) return null
      temporary = false
      return { event: 'close', options: { restoreFocus: false } }
    },
    close() { temporary = false; pinned = false },
  }
}

// Some mobile media elements expose volume while ignoring writes. Probe before
// playback, then restore the original value rather than offering a dead slider.
export function supportsMediaVolume(video, navigatorInfo = globalThis.navigator) {
  if (!video) return false
  const appleMobile = /iPad|iPhone|iPod/i.test(navigatorInfo?.userAgent || navigatorInfo?.platform || '')
    || (navigatorInfo?.platform === 'MacIntel' && Number(navigatorInfo?.maxTouchPoints) > 1)
  if (appleMobile) return false
  let original
  try {
    original = Number(video.volume)
    if (!Number.isFinite(original) || original < 0 || original > 1) return false
    const probe = original <= 0.5 ? 0.75 : 0.25
    video.volume = probe
    return Math.abs(Number(video.volume) - probe) < 0.001
  } catch {
    return false
  } finally {
    if (Number.isFinite(original)) {
      try { video.volume = original } catch { /* Read-only mobile volume. */ }
    }
  }
}
