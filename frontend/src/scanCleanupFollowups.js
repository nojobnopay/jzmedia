import { shallowRef } from 'vue'

export const SCAN_CLEANUP_STORAGE_KEY = 'jzmedia:scan-cleanup-followups:v1'
const positiveId = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null
const count = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : 0
const validJob = value => typeof value === 'string' && value.length > 0 && value.length <= 160
const empty = () => ({ version: 1, entries: {}, seen: [] })

// Use per-library counters, never a whole-media-library total for one selected tab.
export function scanCleanupChanges(job) {
  if (job?.state !== 'done' || !validJob(job.job_id)) return {}
  const summary = job.summary || {}, library = positiveId(job.library_id)
  const buckets = summary.by_library || (library && job.media_library_id == null ? { [library]: summary.counts || {} } : {})
  const changes = {}
  for (const [key, bucket] of Object.entries(buckets)) {
    const id = positiveId(key)
    if (!id || library && id !== library || !bucket || typeof bucket !== 'object') continue
    if (count(bucket.library_offline) || count(bucket.scan_failed)
      || Object.keys(bucket).some(name => name.startsWith('error') && count(bucket[name]))) continue
    // Older responses without per-library errors cannot prove a complete scan.
    if (!summary.by_library && summary.errors?.length) continue
    const removed = count(bucket.removed_movie) + count(bucket.removed_episode)
    const changed = count(bucket.replaced_episode) + count(bucket.changed_episode)
    if (removed || changed) changes[id] = { removed, changed }
  }
  return changes
}

export function cleanupResolved(result) {
  if (!result || count(result.failed) || Object.values(result.skipped || {}).some(count)) return false
  if (Object.values(result.skipped_details || {}).some(count)) return false
  if (!Number.isSafeInteger(result.candidates) || result.candidates < 0) return false
  return result.candidates === 0 || result.dry_run === false && Number(result.removed) >= result.candidates
}

// These are reminders in this browser, not an authorization to remove any cache.
// A new scan must not be dismissed by a cleanup started against an older reminder.
export function createScanCleanupFollowups({ storage = () => typeof window === 'undefined' ? null : window.localStorage, now = Date.now } = {}) {
  const state = shallowRef(empty())
  let memoryOnly = false
  function read() {
    if (memoryOnly) return state.value
    try {
      const target = storage()
      if (!target) return state.value
      const raw = JSON.parse(target.getItem(SCAN_CLEANUP_STORAGE_KEY) || 'null')
      if (raw?.version !== 1 || !raw.entries || typeof raw.entries !== 'object' || Array.isArray(raw.entries)) return empty()
      const entries = {}
      for (const [key, entry] of Object.entries(raw.entries).slice(-64)) {
        const id = positiveId(key)
        if (!id || !validJob(entry?.jobId) || !Number.isFinite(entry.createdAt)) continue
        if (count(entry.removed) || count(entry.changed)) entries[id] = {
          jobId: entry.jobId, removed: count(entry.removed), changed: count(entry.changed), createdAt: entry.createdAt,
        }
      }
      return { version: 1, entries, seen: Array.isArray(raw.seen) ? raw.seen.filter(validJob).slice(-32) : [] }
    } catch { return state.value }
  }
  function persist(next) {
    state.value = next
    try { storage()?.setItem(SCAN_CLEANUP_STORAGE_KEY, JSON.stringify(next)) } catch { memoryOnly = true }
  }
  function reload() { state.value = read() }
  function record(job) {
    if (job?.state !== 'done' || !validJob(job.job_id)) return
    const current = read()
    if (current.seen.includes(job.job_id)) { state.value = current; return }
    const entries = { ...current.entries }
    for (const [id, changes] of Object.entries(scanCleanupChanges(job))) {
      entries[id] = { ...changes, jobId: job.job_id, createdAt: now() }
    }
    const limited = Object.entries(entries).sort(([, left], [, right]) => right.createdAt - left.createdAt).slice(0, 64)
    persist({ version: 1, entries: Object.fromEntries(limited), seen: [...current.seen, job.job_id].slice(-32) })
  }
  function snapshot() { return { ...state.value.entries } }
  function settle(result, reviewed) {
    if (!cleanupResolved(result)) return false
    const current = read(), entries = { ...current.entries }
    for (const [id, entry] of Object.entries(reviewed || {})) {
      if (entries[id]?.jobId === entry.jobId) delete entries[id]
    }
    persist({ ...current, entries })
    return true
  }
  reload()
  return { state, reload, record, snapshot, settle }
}

export const scanCleanupFollowups = createScanCleanupFollowups()
