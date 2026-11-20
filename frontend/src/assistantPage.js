import { onScopeDispose, shallowRef, watchEffect } from 'vue'

export const assistantPage = shallowRef(null)
export const assistantAccess = Symbol('assistantAccess')
let currentOwner

// A previous route can finish updating or unmounting after the next one registers.
export function useAssistantPage(channel, getState, getDiagnosis) {
  const owner = Symbol(channel)
  currentOwner = owner
  watchEffect(() => {
    if (owner !== currentOwner) return
    const { tips, blocked = false } = getState()
    assistantPage.value = { channel, tips, blocked, ...(getDiagnosis ? { diagnosis: getDiagnosis() } : {}) }
  })
  onScopeDispose(() => {
    if (owner !== currentOwner) return
    currentOwner = null
    assistantPage.value = null
  })
}
