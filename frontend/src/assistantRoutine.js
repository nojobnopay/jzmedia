// A single timeline serves every channel. Navigation can discard an event, but
// cannot shorten the quiet period left by the preceding tip or gesture.
const QUIET = 60_000
const SLEEP_AFTER = 180_000
const TIP_DURATION = 8_000
const GESTURE_DURATION = 1_850

export function createAssistantRoutine(now = Date.now, random = Math.random) {
  const nextGesture = () => now() + QUIET + Math.floor(random() * 45_000)
  let lastInput = now(), quietUntil = 0, gestureAt = nextGesture()
  let candidateId, candidateSince = 0
  let active = null, endAt = 0, offered = false, previousGesture = ''
  function stop() {
    if (active) quietUntil = now() + QUIET
    active = null
    return null
  }
  function activity(keepTip = false) {
    lastInput = now()
    if (keepTip && active?.phase === 'tip') return active
    return stop()
  }
  function visit() {
    activity()
    candidateId = undefined
    offered = false
  }
  function resume() {
    activity()
    gestureAt = nextGesture()
  }
  function opened() {
    activity()
    offered = true
    quietUntil = now() + QUIET
  }
  function tick({ tip, motion }) {
    const time = now()
    // A search or a late library response can reveal a tip well after arrival.
    // Drop that opportunity if another gesture keeps it waiting too long.
    if (tip?.id !== candidateId) {
      candidateId = tip?.id
      candidateSince = time
    }
    if (active) {
      if (active.phase === 'tip' && active.id !== tip?.id) return stop()
      if (active.phase !== 'tip' && !motion) return stop()
      if (time >= endAt) return stop()
      return active
    }
    if (time < quietUntil || time - lastInput < 4_000) return null
    if (!offered && tip && time - candidateSince < 30_000) {
      offered = true
      active = { phase: 'tip', id: tip.id }
      endAt = time + TIP_DURATION
    } else if (motion && time - lastInput >= SLEEP_AFTER) {
      active = { phase: 'ambient-sleep' }
      endAt = Infinity
    } else if (motion && time >= gestureAt && time - lastInput < SLEEP_AFTER - QUIET - GESTURE_DURATION) {
      const wave = previousGesture ? previousGesture !== 'ambient-wave' : random() < .5
      previousGesture = wave ? 'ambient-wave' : 'ambient-frown'
      active = { phase: previousGesture }
      endAt = time + GESTURE_DURATION
      gestureAt = nextGesture()
    }
    return active
  }
  return { activity, visit, opened, resume, tick }
}
