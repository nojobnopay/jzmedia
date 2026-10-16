import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { allocationRange, cacheBudget, capacityError, GIB, moveAllocationBoundary, reservedTenths } from '../src/cacheBudget.js'

const cases = JSON.parse(readFileSync(new URL('../../tests/fixtures/cache_budget.json', import.meta.url)))
test('reserve calculation matches backend fixtures at exact byte boundaries', () => {
  for (const item of cases) assert.equal(reservedTenths(item.base_bytes), item.reserved_tenths, item.name)
})

const limits = { data_warning_gb: 100, transcode_cache_gb: 40, preview_cache_gb: 30 }
const usage = { ok: true, total_bytes: 25 * GIB,
  categories: { database: GIB, posters: 4 * GIB, other: 0, transcode: 10 * GIB, previews: 10 * GIB } }
test('budget allocates 6/40/30/24 and distinguishes reservation from current usage', () => {
  const result = cacheBudget(limits, usage)
  assert.equal(result.baseBytes, 5 * GIB)
  assert.equal(result.reservedGb, 6)
  assert.deepEqual(result.segments.map(({ key, gb, percent }) => ({ key, gb, percent })), [
    { key: 'base', gb: 6, percent: 6 }, { key: 'transcode', gb: 40, percent: 40 },
    { key: 'previews', gb: 30, percent: 30 }, { key: 'free', gb: 24, percent: 24 },
  ])
  assert.equal(result.valid, true)
  assert.equal(result.editable, true)
  assert.equal(result.minimumTotalGb, 6.2)
  assert.equal(result.scaleGb, 100)
  assert.equal(result.error, '')
})
test('overbudget and growing base data do not mutate draft allocations', () => {
  const draft = { ...limits, data_warning_gb: 75.9 }
  const result = cacheBudget(draft, usage)
  assert.equal(result.valid, false)
  assert.equal(result.editable, true)
  assert.equal(result.scaleGb, 76)
  assert.equal(result.excessGb, 0.1)
  assert.match(result.error, /超出预算 0.1 GB/)
  assert.equal(draft.transcode_cache_gb, 40)
  assert.equal(draft.preview_cache_gb, 30)
  const grown = cacheBudget(limits, { ...usage, categories: { ...usage.categories, database: 40 * GIB, posters: 0 } })
  assert.equal(grown.reservedGb, 44)
  assert.equal(grown.excessGb, 14)
})
test('exact budget, including decimal values, saves even if actual cache usage exceeds it', () => {
  assert.equal(cacheBudget({ ...limits, data_warning_gb: 76 }, usage).valid, true)
  assert.equal(cacheBudget({ data_warning_gb: 8, transcode_cache_gb: 1, preview_cache_gb: 1 }, usage).valid, true)
  assert.equal(cacheBudget({ data_warning_gb: 1.3, transcode_cache_gb: 0.1, preview_cache_gb: 0.2 }, {
    ok: true, categories: { database: 0, posters: 0, other: 0, transcode: 0, previews: 0 },
  }).valid, true)
})
test('unknown usage and legacy unlimited playback prevent claiming a valid allocation', () => {
  for (const value of [null, { ok: false }, { ok: true, categories: {} }, {
    ok: true, categories: { database: NaN, posters: 0, other: 0 },
  }]) {
    const result = cacheBudget(limits, value)
    assert.equal(result.available, false)
    assert.equal(result.valid, false)
    assert.equal(result.editable, false)
  }
  const result = cacheBudget({ ...limits, transcode_cache_gb: 0 }, usage)
  assert.equal(result.valid, false)
  assert.equal(result.editable, false)
  assert.deepEqual(result.segments, [])
  assert.match(result.error, /未限制容量/)
})
test('capacity errors handle empty, non-finite, range and decimal input', () => {
  for (const value of ['', null, NaN, Infinity, -1, 0, 102400.1, 0.15]) assert.ok(capacityError(value, 0.1))
  for (const value of [0.1, 1, 76, 102400]) assert.equal(capacityError(value, 0.1), '')
})

test('playback handle changes its own quota and free space without changing previews', () => {
  const moved = moveAllocationBoundary(limits, usage, 'transcode', 50)
  assert.deepEqual(moved, { ...limits, transcode_cache_gb: 50 })
  assert.equal(cacheBudget(moved, usage).freeGb, 14)
  assert.deepEqual(allocationRange(limits, usage, 'transcode'), { min: 0.1, max: 102400, value: 40 })
  assert.deepEqual(moveAllocationBoundary(limits, usage, 'transcode', -1), { ...limits, transcode_cache_gb: 0.1 })
  let draft = { ...limits }
  for (let i = 0; i < 100; i++) draft = moveAllocationBoundary(draft, usage, 'transcode', draft.transcode_cache_gb + 0.1)
  assert.deepEqual(draft, moved)
  assert.deepEqual(limits, { data_warning_gb: 100, transcode_cache_gb: 40, preview_cache_gb: 30 })
})

test('preview handle changes its own quota and free space without changing playback', () => {
  const moved = moveAllocationBoundary(limits, usage, 'previews', 40)
  assert.deepEqual(moved, { ...limits, preview_cache_gb: 40 })
  assert.equal(cacheBudget(moved, usage).freeGb, 14)
  assert.deepEqual(allocationRange(limits, usage, 'previews'), { min: 0.1, max: 102400, value: 30 })
  const capped = moveAllocationBoundary(limits, usage, 'previews', 1000)
  assert.equal(capped.preview_cache_gb, 1000)
  assert.equal(capped.transcode_cache_gb, 40)
  assert.equal(cacheBudget(capped, usage).freeGb, 0)
  assert.equal(cacheBudget(capped, usage).excessGb, 946)
  assert.equal(cacheBudget(capped, usage).editable, true)
  assert.equal(moveAllocationBoundary(limits, usage, 'previews', -4).preview_cache_gb, 0.1)
  assert.equal(moveAllocationBoundary(limits, usage, 'previews', 30.16).preview_cache_gb, 30.2)
})

test('each handle can repair 21.3 GB allocated against a 20 GB budget', () => {
  const stats = { ...usage, categories: { ...usage.categories, database: GIB / 4, posters: 0, other: 0 } }
  const over = { data_warning_gb: 20, transcode_cache_gb: 10, preview_cache_gb: 10 }
  const budget = cacheBudget(over, stats)
  assert.equal(budget.reservedGb, 1.3)
  assert.equal(budget.allocatedGb, 21.3)
  assert.equal(budget.scaleGb, 21.3)
  assert.equal(budget.editable, true)
  assert.equal(budget.valid, false)
  for (const [part, key, other] of [['transcode', 'transcode_cache_gb', 'preview_cache_gb'],
    ['previews', 'preview_cache_gb', 'transcode_cache_gb']]) {
    assert.deepEqual(allocationRange(over, stats, part), { min: 0.1, max: 102400, value: 10 })
    const fixed = moveAllocationBoundary(over, stats, part, 8.7)
    assert.equal(fixed[key], 8.7)
    assert.equal(fixed[other], 10)
    assert.equal(cacheBudget(fixed, stats).allocatedGb, 20)
    assert.equal(cacheBudget(fixed, stats).scaleGb, 20)
    assert.equal(cacheBudget(fixed, stats).valid, true)
  }
  assert.deepEqual(over, { data_warning_gb: 20, transcode_cache_gb: 10, preview_cache_gb: 10 })
})

test('temporary new over-allocation stays editable and can be repaired without changing the other quota', () => {
  for (const [part, key, other, target] of [['transcode', 'transcode_cache_gb', 'preview_cache_gb', 64],
    ['previews', 'preview_cache_gb', 'transcode_cache_gb', 54]]) {
    const over = moveAllocationBoundary(limits, usage, part, target + 0.1)
    const budget = cacheBudget(over, usage)
    assert.equal(over[other], limits[other])
    assert.equal(budget.excessGb, 0.1)
    assert.equal(budget.freeGb, 0)
    assert.equal(budget.valid, false)
    assert.equal(budget.editable, true)
    const increasedAgain = moveAllocationBoundary(over, usage, part, target + 1)
    assert.equal(increasedAgain[key], target + 1, 'existing excess does not impose a new gesture ceiling')
    assert.equal(cacheBudget(increasedAgain, usage).excessGb, 1)
    const fixed = moveAllocationBoundary(increasedAgain, usage, part, target)
    assert.equal(fixed[other], limits[other])
    assert.equal(cacheBudget(fixed, usage).valid, true)
    assert.equal(cacheBudget(fixed, usage).editable, true)
  }
})

test('both quota handles honor tenths and hard limits independently of the total budget', () => {
  for (const [part, key, other] of [['transcode', 'transcode_cache_gb', 'preview_cache_gb'],
    ['previews', 'preview_cache_gb', 'transcode_cache_gb']]) {
    for (const request of [102400, 102400.1, Number.MAX_VALUE]) {
      const maximum = moveAllocationBoundary(limits, usage, part, request)
      assert.equal(maximum[key], 102400)
      assert.equal(maximum[other], limits[other])
      assert.equal(cacheBudget(maximum, usage).editable, true)
      assert.equal(cacheBudget(maximum, usage).valid, false)
    }
    assert.equal(moveAllocationBoundary(limits, usage, part, 0)[key], 0.1)
    assert.equal(moveAllocationBoundary(limits, usage, part, 0.15)[key], 0.2)
    for (const value of [NaN, Infinity, -Infinity, null, '30'])
      assert.deepEqual(moveAllocationBoundary(limits, usage, part, value), limits)
  }
  const tiny = { data_warning_gb: 6.2, transcode_cache_gb: 0.1, preview_cache_gb: 0.1 }
  for (const part of ['transcode', 'previews']) {
    assert.deepEqual(allocationRange(tiny, usage, part), { min: 0.1, max: 102400, value: 0.1 })
    const over = moveAllocationBoundary(tiny, usage, part, 0.2)
    assert.equal(cacheBudget(over, usage).excessGb, 0.1)
    assert.equal(cacheBudget(over, usage).editable, true)
    assert.deepEqual(moveAllocationBoundary(over, usage, part, 0.1), tiny)
  }
})

test('invalid numbers or incomplete statistics disable editing without rewriting the draft', () => {
  const invalid = []
  for (const key of ['data_warning_gb', 'transcode_cache_gb', 'preview_cache_gb']) {
    for (const value of ['', null, NaN, Infinity, 0, 0.15, 102400.1]) invalid.push([{ ...limits, [key]: value }, usage])
  }
  invalid.push([limits, null], [limits, { ...usage, ok: false }])
  for (const key of Object.keys(usage.categories)) {
    const categories = { ...usage.categories }
    delete categories[key]
    invalid.push([limits, { ...usage, categories }])
    for (const value of [-1, 0.5, NaN, Number.MAX_SAFE_INTEGER + 1])
      invalid.push([limits, { ...usage, categories: { ...usage.categories, [key]: value } }])
  }
  invalid.push([limits, { ...usage, categories: { ...usage.categories,
    database: Number.MAX_SAFE_INTEGER, posters: Number.MAX_SAFE_INTEGER } }])
  for (const [draft, stats] of invalid) {
    const before = { ...draft }
    assert.equal(cacheBudget(draft, stats).editable, false)
    assert.equal(cacheBudget(draft, stats).valid, false)
    for (const part of ['transcode', 'previews']) {
      assert.equal(allocationRange(draft, stats, part), null)
      assert.deepEqual(moveAllocationBoundary(draft, stats, part, 12), draft)
    }
    assert.deepEqual(draft, before)
  }
  assert.deepEqual(moveAllocationBoundary(limits, usage, 'unknown', 12), limits)
})

test('base reservation plus minimum quotas explains the smallest possible total', () => {
  for (const total of [5, 6.1]) {
    const draft = { data_warning_gb: total, transcode_cache_gb: 0.1, preview_cache_gb: 0.1 }
    const result = cacheBudget(draft, usage)
    assert.equal(result.reservedGb, 6)
    assert.equal(result.minimumTotalGb, 6.2)
    assert.equal(result.editable, true)
    assert.equal(result.valid, false)
    assert.match(result.error, /至少需要 6.2 GB 总预算/)
    assert.match(result.error, /仅调低缓存额度仍不足/)
    assert.deepEqual(moveAllocationBoundary(draft, usage, 'transcode', 0), draft)
  }
  const result = cacheBudget({ data_warning_gb: 6.2, transcode_cache_gb: 0.1, preview_cache_gb: 0.1 }, usage)
  assert.equal(result.minimumTotalGb, 6.2)
  assert.equal(result.valid, true)
  assert.equal(result.error, '')
})

test('base data exceeding the total field maximum is never silently reduced to fit', () => {
  const stats = { ...usage, categories: { ...usage.categories, database: 102400 * GIB, posters: 0, other: 0 } }
  const draft = { data_warning_gb: 102400, transcode_cache_gb: 0.1, preview_cache_gb: 0.1 }
  const result = cacheBudget(draft, stats)
  assert.equal(result.reservedGb, 112640)
  assert.equal(result.minimumTotalGb, 112640.2)
  assert.equal(result.scaleGb, 112640.2)
  assert.equal(result.editable, true)
  assert.equal(result.valid, false)
  assert.match(result.error, /超过可配置上限 102400 GB/)
  assert.match(result.error, /无法仅通过增加总预算解决/)
  assert.match(result.error, /减少基础数据占用/)
  assert.equal(stats.categories.database, 102400 * GIB)
})
