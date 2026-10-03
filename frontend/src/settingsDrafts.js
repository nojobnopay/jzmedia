import { computed, inject, onScopeDispose, ref, shallowRef, watch } from 'vue'

export const SETTINGS_DRAFTS = Symbol('settings drafts')

// Forms register only under Settings; shared onboarding forms keep their own lifecycle.
export function useSettingsDraft(entry) {
  const guard = inject(SETTINGS_DRAFTS, null)
  if (!guard) return
  const unregister = guard.register(entry)
  onScopeDispose(unregister)
}

export function createSettingsDraftGuard() {
  const entries = new Set()
  const revision = ref(0)
  const pending = shallowRef(null)
  const approved = new Map()
  const selected = section => {
    revision.value
    return [...entries].filter(entry => !section || entry.section === section)
  }
  const needsAttention = entry => !!entry.dirty?.() || !!entry.busy?.()
  const protectedState = computed(() => selected().some(needsAttention))
  const dialogBusy = computed(() => !!pending.value?.entries.some(entry => entry.busy?.()))
  const dialogItems = computed(() => (pending.value?.entries || []).filter(needsAttention).flatMap(entry =>
    entry.items?.() || [{ label: entry.label, edit: entry.edit }]))

  // A successful save while the dialog is open makes this attempted departure
  // obsolete. Stay on the current page rather than showing an empty dirty list.
  const stopWatching = watch(() => pending.value && pending.value.entries.some(needsAttention), remaining => {
    if (remaining === false) stay()
  }, { flush: 'sync' })

  function register(entry) {
    entries.add(entry)
    revision.value++
    return () => { entries.delete(entry); revision.value++ }
  }
  function requestLeave(to, section) {
    // A second navigation must not inherit another destination's confirmation.
    if (pending.value) return false
    const affected = selected(section).filter(needsAttention)
    if (!affected.length) return true
    return new Promise(resolve => { pending.value = { to, entries: affected, resolve } })
  }
  function stay() {
    const request = pending.value
    pending.value = null
    request?.resolve(false)
  }
  function discardAndLeave() {
    const request = pending.value
    if (!request || dialogBusy.value) return
    // Another router guard may still refuse this navigation (e.g. file review).
    // Only commit discards in afterEach once the entire navigation succeeds.
    approved.set(request.to, request.entries)
    pending.value = null
    request.resolve(true)
  }
  function finishNavigation(to, failure) {
    const accepted = approved.get(to)
    approved.delete(to)
    if (!failure) accepted?.forEach(entry => { if (entry.dirty?.()) entry.discard() })
  }
  function beforeUnload(event) {
    if (!protectedState.value) return
    event.preventDefault()
    event.returnValue = ''
  }
  function dispose() { stopWatching(); stay(); approved.clear(); entries.clear(); revision.value++ }
  return { register, pending, dialogBusy, dialogItems, protectedState, requestLeave, stay, discardAndLeave, finishNavigation, beforeUnload, dispose }
}
