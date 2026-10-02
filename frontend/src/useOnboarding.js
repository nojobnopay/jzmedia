import { onScopeDispose, ref } from 'vue'
import { api } from './api.js'

export function useOnboarding(request = api) {
  const state = ref(null)
  const busy = ref(false)
  const error = ref('')
  let generation = 0
  let disposed = false
  async function refresh() {
    if (busy.value || disposed) return null
    const gen = ++generation
    try {
      const data = await request('/api/onboarding')
      if (disposed || gen !== generation) return null
      state.value = data; error.value = ''
      return data
    } catch (e) {
      if (!disposed && gen === generation) error.value = '引导状态加载失败：' + e.message
      return null
    }
  }
  async function save(changes) {
    if (busy.value || disposed) return null
    busy.value = true; error.value = ''
    const gen = ++generation
    try {
      const data = await request('/api/onboarding', {
        method: 'PATCH', body: JSON.stringify(changes),
      })
      if (disposed || gen !== generation) return null
      state.value = data
      return data
    } catch (e) {
      if (!disposed && gen === generation) error.value = '进度保存失败：' + e.message
      return null
    } finally { if (!disposed) busy.value = false }
  }
  onScopeDispose(() => { disposed = true; generation++ })
  return { state, busy, error, refresh, save }
}
