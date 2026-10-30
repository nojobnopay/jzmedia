import assert from 'node:assert/strict'
import test from 'node:test'
import { assertFixtureIdle, waitForFixtureIdle, waitUntil } from '../scripts/online_fixture/browser.mjs'

function idleMetrics(kind = 'catalog') {
  const metrics = {
    registry: { sessions: 0, pending_sessions: 0, active_requests: 0, reserved_bytes: 0,
      resources: 0, resource_url_bytes: 0, consumed_bytes: 4096 },
    local_rows: { movies: 0, tv_episodes: 0, playback_progress: 0 },
  }
  if (kind === 'player') metrics.pending_resolutions = 0
  else Object.assign(metrics, { pending_requests: 0, catalogue_active: 0, catalogue_owners: 0,
    api_pool: { active: 0, idle: 1 }, image_pool: { active: 0, idle: 1 }, images: { active: 0, peak: 2 },
    artwork_cache: { inflight: 0, pending: 0, bytes: 512 },
    admission: { active: 0, owners: 0, background: 0, preempt_waiters: 0 } })
  return metrics
}

const commonResources = ['registry.sessions', 'registry.pending_sessions', 'registry.active_requests',
  'registry.reserved_bytes', 'registry.resources', 'registry.resource_url_bytes',
  'local_rows.movies', 'local_rows.tv_episodes', 'local_rows.playback_progress']
const catalogResources = ['pending_requests', 'catalogue_active', 'catalogue_owners', 'api_pool.active',
  'image_pool.active', 'artwork_cache.inflight', 'artwork_cache.pending', 'images.active',
  'admission.active', 'admission.owners', 'admission.background', 'admission.preempt_waiters']
function field(metrics, name) {
  const keys = name.split('.'), key = keys.pop()
  return { parent: keys.reduce((object, key) => object[key], metrics), key }
}

test('player may omit catalogue telemetry; catalogue requires its complete resource contract', () => {
  assert.doesNotThrow(() => assertFixtureIdle(idleMetrics('player'), 'player'))
  assert.doesNotThrow(() => assertFixtureIdle(idleMetrics(), 'catalog'))
  assert.throws(() => assertFixtureIdle(idleMetrics('player'), 'catalog'), /pending_requests/)
  assert.throws(() => assertFixtureIdle(idleMetrics(), 'player'), /pending_resolutions/)
  assert.throws(() => assertFixtureIdle(idleMetrics(), 'state'), /Unknown online fixture kind/)
  assert.throws(() => assertFixtureIdle(null, 'catalog'), /registry.sessions/)
})

for (const kind of ['player', 'catalog']) {
  const resources = [...commonResources, ...(kind === 'player' ? ['pending_resolutions'] : catalogResources)]
  test(`${kind}: every live resource or unexpected local row prevents an idle pass`, () => {
    for (const name of resources) {
      const metrics = idleMetrics(kind), { parent, key } = field(metrics, name)
      parent[key] = 1
      assert.throws(() => assertFixtureIdle(metrics, kind), error => error.message.includes(name), name)
    }
  })
  test(`${kind}: missing, negative and malformed resource measurements never count as zero`, () => {
    for (const name of resources) {
      for (const invalid of [undefined, null, '0', false, -1, NaN, Infinity, 0.5]) {
        const metrics = idleMetrics(kind), { parent, key } = field(metrics, name)
        if (invalid === undefined) delete parent[key]
        else parent[key] = invalid
        assert.throws(() => assertFixtureIdle(metrics, kind), error => error.message.includes(name), name)
      }
    }
  })
}

test('release polling waits for outstanding reservations and preserves final complete telemetry', async () => {
  let reads = 0
  const result = await waitForFixtureIdle(async () => {
    const metrics = idleMetrics()
    if (++reads === 1) metrics.registry.pending_sessions = 1
    return metrics
  }, 'catalog', { timeout: 1000 })
  assert.equal(reads, 2)
  assert.equal(result.metrics.registry.pending_sessions, 0)
  assert.ok(result.releaseMs >= 0)
})

test('release timeout reports the missing measurement instead of passing incomplete evidence', async () => {
  await assert.rejects(waitForFixtureIdle(async () => {
    const metrics = idleMetrics()
    delete metrics.artwork_cache.pending
    return metrics
  }, 'catalog', { message: 'Fixture did not release', timeout: 100 }), /Fixture did not release.*artwork_cache.pending/)
})

test('cancellation escapes polling immediately without swallowing the stop signal', async () => {
  let reads = 0
  await assert.rejects(waitUntil(() => { reads++; return false }, 'never reached', {
    checkStop: () => { throw new Error('Validation interrupted') },
  }), /Validation interrupted/)
  assert.equal(reads, 0)
})
