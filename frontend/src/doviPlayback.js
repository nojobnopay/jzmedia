export function isDoviP5Playback(plan, media, reasons = []) {
  return plan?.color_pipeline === 'dovi_p5_sdr' || Number(media?.dv_profile) === 5
    || reasons.some(reason => reason.startsWith('dovi_p5_'))
}

// A color failure is final for this lease. Only a slow 1080p automatic stream may
// be replaced once; explicit quality choices are never changed behind the user.
export function doviHeartbeatAction(result, { p5, quality, height, downgraded }) {
  if (!p5 || !result) return null
  const failed = result.failed || result.failure_code || result.failure_reason
  if (result.complete && !failed) return null
  if (failed && result.failure_code !== 'dovi_too_slow') return 'stop'
  if (Number(result.recommended_height) === 720 && quality === 'auto'
      && Number(height) === 1080 && !downgraded) return 'downgrade'
  return failed ? 'stop' : null
}

export function compatiblePlaybackVersion(versions = [], currentId) {
  const candidates = versions.filter(version => Number(version.version_id) !== Number(currentId)
    && version.playable && version.method !== 'blocked' && Number(version.dv_profile) !== 5)
  const rank = method => ({ direct: 0, remux: 1, audio_transcode: 2, video_transcode: 3 })[method] ?? 9
  return candidates.sort((a, b) => rank(a.method) - rank(b.method)
    || Number(a.version_id) - Number(b.version_id))[0] || null
}
