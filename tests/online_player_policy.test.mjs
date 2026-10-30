import assert from 'node:assert/strict'
import test from 'node:test'
import { assessRelaySamples, canAllowStartupGap } from '../scripts/online_diagnostics/player_policy.mjs'

const gap = { type: 'mediaError', detail: 'bufferSeekOverHole', fatal: false,
  beforeFirstFrame: true, priorRecoveries: 0, mediaTime: 0.195778,
  seekFrom: 0, seekTo: 0.195778 }

test('one bounded initial alignment is eligible for recovered classification', () => {
  assert.equal(canAllowStartupGap(gap), true)
  assert.equal(canAllowStartupGap({ ...gap, mediaTime: 0.5, seekTo: 0.5 }), true)
})

for (const [name, change] of Object.entries({
  repeated: { priorRecoveries: 1 }, afterFrame: { beforeFirstFrame: false }, fatal: { fatal: true },
  wrongDetail: { detail: 'bufferStalledError' }, wrongType: { type: 'networkError' },
  largeJump: { mediaTime: 0.6, seekTo: 0.6 }, negativeFrom: { seekFrom: -0.01 },
  backwardJump: { seekFrom: 0.3 }, missingTime: { mediaTime: null },
  infiniteTarget: { seekTo: Infinity }, negativeTime: { mediaTime: -0.1 },
  inconsistentTarget: { mediaTime: 0.4 }, unparsedStart: { seekFrom: null },
})) test(`initial-gap exception rejects ${name}`, () => {
  assert.equal(canAllowStartupGap({ ...gap, ...change }), false)
})

function metrics(time, extra = {}) {
  return { active: 0, peak_active: 2, max_media_requests: 4, queued: 0,
    peak_queued: 0, max_queued_requests: 8, max_http_handlers: 16, bytes_sent: time * 100,
    budget_charged_bytes: time * 100, max_bytes_total: 100000, upstream_errors: 0,
    rejected_busy: 0, rejected_budget: 0, rejected_handlers: 0, cpu_seconds: time * 0.01,
    rss_kib: 40000, uptime_seconds: time, process_write_bytes: 0, pid: 1234,
    child_pids: [], ...extra }
}
function samples() {
  return ['baseline', 'playback', 'playback', 'after_close'].map((phase, index) => ({
    phase, at: `sample-${index}`, value: metrics(index + 1),
  }))
}

test('complete owned-relay evidence passes cleanly after release', () => {
  const result = assessRelaySamples(samples())
  assert.equal(result.passed, true)
  assert.equal(result.classification, 'clean')
  assert.equal(result.phases.playback.seconds, 1)
  assert.equal(result.release.requiresOrchestrator, false)
})

test('interior metric timeout is an explicit observation gap, not a resource failure or clean pass', () => {
  const input = samples()
  input.splice(2, 0, { phase: 'playback', at: 'gap', value: { fetchError: 'TimeoutError' } })
  const result = assessRelaySamples(input)
  assert.equal(result.passed, null)
  assert.equal(result.classification, 'observation_gaps')
  assert.equal(result.observationGaps.length, 1)
  assert.equal(result.observationGaps[0].fetchError, 'TimeoutError')
  assert.deepEqual(result.reasons, [])
  assert.equal(result.phases.playback, undefined) // No inferred CPU across the gap.
})

for (const index of [0, 3]) test(`critical boundary sample ${index} cannot be skipped`, () => {
  const input = samples(); input[index].value = { fetchError: 'TimeoutError' }
  const result = assessRelaySamples(input)
  assert.equal(result.passed, false)
  assert.ok(result.reasons.includes('critical_boundary_metrics_missing'))
})

test('shared relay preserves active observations and delegates final release', () => {
  const input = samples(); input.at(-1).value.active = 1
  const result = assessRelaySamples(input, { sharedRelay: true })
  assert.equal(result.passed, null)
  assert.equal(result.classification, 'awaiting_shared_release')
  assert.equal(result.release.requiresOrchestrator, true)
  assert.equal(result.release.observedIdle, false)
  assert.deepEqual(result.reasons, [])
  assert.ok(assessRelaySamples(input).reasons.includes('connections_not_released'))
})

for (const [name, change, reason] of [
  ['upstream error', { upstream_errors: 1 }, 'upstream_errors_increased'],
  ['overload', { rejected_busy: 1 }, 'rejected_busy_increased'],
  ['budget rejection', { rejected_budget: 1 }, 'rejected_budget_increased'],
  ['handler rejection', { rejected_handlers: 1 }, 'rejected_handlers_increased'],
  ['concurrency breach', { peak_active: 5 }, 'concurrency_limit_exceeded'],
  ['queue breach', { peak_queued: 9 }, 'queue_limit_exceeded'],
  ['byte breach', { budget_charged_bytes: 100001 }, 'byte_limit_exceeded'],
  ['disk write', { process_write_bytes: 4096 }, 'storage_writes'],
  ['child process', { child_pids: [5678] }, 'relay_child_processes'],
  ['process restart', { pid: 3456 }, 'relay_process_changed'],
]) test(`${name} still fails even with a missing observation`, () => {
  const input = samples()
  input.splice(2, 0, { phase: 'playback', value: { fetchError: 'TimeoutError' } })
  input.at(-1).value = { ...input.at(-1).value, ...change }
  const result = assessRelaySamples(input, { sharedRelay: true })
  assert.equal(result.passed, false)
  assert.equal(result.classification, 'failed')
  assert.ok(result.reasons.includes(reason))
})

test('an error counter cannot reset between valid observations', () => {
  const input = samples(); input[1].value.upstream_errors = 1
  assert.ok(assessRelaySamples(input).reasons.includes('upstream_errors_reset'))
})

test('malformed and missing resource metrics are observations gaps, never clean', () => {
  for (const change of [{ rss_kib: NaN }, { active: -1 }, { pid: 0 }, { child_pids: null }]) {
    const input = samples(); input[1].value = { ...input[1].value, ...change }
    assert.equal(assessRelaySamples(input).classification, 'observation_gaps')
  }
})
