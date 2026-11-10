import { inject, nextTick, onScopeDispose, ref, watch } from 'vue'
import { SETTINGS_DRAFTS } from './settingsDrafts.js'

// Editing stays in the owning panel so its draft and request lifecycle survive.
// Returning to the list is an explicit departure, guarded like a settings route.
export function useSettingsTaskNavigation({ section, active, busy, close }) {
  const guard = inject(SETTINGS_DRAFTS, null)
  const heading = ref(null)
  const listHeading = ref(null)
  let origin = null, scrollY = 0, disposed = false

  async function focusTask() {
    await nextTick()
    if (disposed || !active.value || !heading.value?.getClientRects().length) return
    heading.value.focus({ preventScroll: true })
    heading.value.scrollIntoView({ block: 'start' })
  }

  watch(active, async (value, previous) => {
    if (typeof document === 'undefined') return
    if (value && !previous) {
      origin = document.activeElement
      scrollY = window.scrollY
      await focusTask()
    } else if (!value && previous) {
      await nextTick()
      // A route guard may discard this task after another settings section has
      // become visible. Hidden panels must not steal its scroll or focus.
      if (disposed || active.value || !listHeading.value?.getClientRects().length) return
      window.scrollTo({ top: scrollY })
      const target = origin?.isConnected && origin.getClientRects().length && !origin.matches(':disabled') ? origin : listHeading.value
      target?.focus({ preventScroll: true })
      origin = null
    }
  })

  async function requestClose() {
    if (busy?.()) return false
    const destination = {}
    if (guard && !await guard.requestLeave(destination, section)) return false
    if (disposed) { guard?.finishNavigation(destination, true); return false }
    guard?.finishNavigation(destination)
    close()
    return true
  }

  onScopeDispose(() => { disposed = true })
  return { heading, listHeading, focusTask, requestClose }
}
