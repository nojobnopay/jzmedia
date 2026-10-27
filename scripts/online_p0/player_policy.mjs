// Pure acceptance rules shared by the P0 browser diagnostic and its tests.
export function canAllowStartupGap(event) {
  return event.type === 'mediaError' && event.detail === 'bufferSeekOverHole'
    && event.fatal === false && event.beforeFirstFrame === true
    && event.priorRecoveries === 0 && Number.isFinite(event.mediaTime)
    && Number.isFinite(event.seekFrom) && Number.isFinite(event.seekTo)
    && event.seekFrom >= 0 && event.seekTo > event.seekFrom && event.seekTo <= 0.5
    && event.seekTo - event.seekFrom <= 0.5
    && event.mediaTime >= 0 && Math.abs(event.mediaTime - event.seekTo) <= 0.01
}

export function assessRelaySamples(samples, { sharedRelay = false } = {}) {
  const counters = ['upstream_errors', 'rejected_busy', 'rejected_budget', 'rejected_handlers']
  const required = ['active', 'peak_active', 'max_media_requests', 'queued', 'peak_queued',
    'max_queued_requests', 'max_http_handlers', 'bytes_sent', 'budget_charged_bytes',
    'max_bytes_total', ...counters, 'cpu_seconds', 'rss_kib', 'uptime_seconds',
    'process_write_bytes', 'pid']
  const gaps = [], valid = []
  for (const [index, sample] of samples.entries()) {
    const missing = required.filter(key => !Number.isFinite(sample.value?.[key]) || sample.value[key] < 0)
    if (!(sample.value?.pid > 0)) missing.push('positive_pid')
    if (!Array.isArray(sample.value?.child_pids)) missing.push('child_pids')
    if (sample.value?.fetchError || missing.length) gaps.push({ index, phase: sample.phase,
      at: sample.at, fetchError: sample.value?.fetchError || null, missingFields: missing })
    else valid.push({ index, ...sample })
  }
  const reasons = [], first = valid[0]?.value, last = valid.at(-1)?.value
  if (!samples.length || samples[0]?.phase !== 'baseline' || valid[0]?.index !== 0 || valid.at(-1)?.index !== samples.length - 1
    || samples.at(-1)?.phase !== 'after_close') reasons.push('critical_boundary_metrics_missing')
  if (valid.length < 3) reasons.push('insufficient_valid_metrics')
  if (!sharedRelay) {
    if (last?.active !== 0) reasons.push('connections_not_released')
    if (last?.queued !== 0) reasons.push('queued_requests_not_released')
  }
  const counterDeltas = {}
  for (const key of counters) {
    counterDeltas[key] = first && last ? last[key] - first[key] : null
    if (counterDeltas[key] > 0) reasons.push(`${key}_increased`)
    if (valid.some((sample, index) => index > 0 && sample.value[key] < valid[index - 1].value[key])) reasons.push(`${key}_reset`)
  }
  if (valid.some(sample => sample.value.pid !== first?.pid)) reasons.push('relay_process_changed')
  if (valid.some(sample => sample.value.peak_active > sample.value.max_media_requests)) reasons.push('concurrency_limit_exceeded')
  if (valid.some(sample => sample.value.peak_queued > sample.value.max_queued_requests)) reasons.push('queue_limit_exceeded')
  if (valid.some(sample => sample.value.budget_charged_bytes > sample.value.max_bytes_total)) reasons.push('byte_limit_exceeded')
  if (valid.some(sample => sample.value.process_write_bytes !== first?.process_write_bytes)) reasons.push('storage_writes')
  if (valid.some(sample => sample.value.child_pids.length)) reasons.push('relay_child_processes')
  const phases = {}
  for (let index = 1; index < valid.length; index++) {
    const current = valid[index], prior = valid[index - 1]
    // Do not manufacture measurements across missing samples or phase changes.
    if (current.index !== prior.index + 1 || current.phase !== prior.phase) continue
    const seconds = current.value.uptime_seconds - prior.value.uptime_seconds
    if (!(seconds > 0)) continue
    const stats = phases[current.phase] ??= { seconds: 0, cpuSeconds: 0, maxRssKiB: 0, bytesSent: 0 }
    stats.seconds += seconds; stats.cpuSeconds += current.value.cpu_seconds - prior.value.cpu_seconds
    stats.maxRssKiB = Math.max(stats.maxRssKiB, current.value.rss_kib, prior.value.rss_kib)
    stats.bytesSent += current.value.bytes_sent - prior.value.bytes_sent
  }
  for (const stats of Object.values(phases)) stats.meanCpuPercentOneCore = stats.cpuSeconds / stats.seconds * 100
  const classification = reasons.length ? 'failed' : gaps.length ? 'observation_gaps'
    : sharedRelay ? 'awaiting_shared_release' : 'clean'
  return { observed: true, passed: reasons.length ? false : gaps.length || sharedRelay ? null : true,
    classification, reasons, observationGaps: gaps, validSamples: valid.length,
    totalSamples: samples.length, first, last, phases, counterDeltas,
    upstreamErrorDelta: counterDeltas.upstream_errors,
    release: { scope: sharedRelay ? 'shared_relay_global' : 'owned_relay',
      observedIdle: last?.active === 0 && last?.queued === 0,
      requiresOrchestrator: sharedRelay },
    note: 'Metrics are global to the relay. Interior observation gaps are explicit and never clean passes; valid boundary samples and resource counters remain required. A shared-relay orchestrator must verify final release after all clients close.' }
}
