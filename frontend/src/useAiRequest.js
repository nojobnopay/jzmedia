import { onScopeDispose, ref } from 'vue'
import { api } from './api.js'

// Each operation owns its request. Even a server ignoring abort cannot restore stale results.
export function useAiRequest(request = api) {
  const busy = ref(false)
  const result = ref(null)
  const error = ref('')
  let generation = 0
  let controller = null
  let disposed = false
  function reset() {
    generation++
    controller?.abort()
    controller = null
    busy.value = false
    result.value = null
    error.value = ''
  }
  async function run(path, payload, method = 'POST') {
    if (disposed || busy.value) return null
    reset()
    const gen = generation
    controller = new AbortController()
    busy.value = true
    try {
      const value = await request(path, {
        method, signal: controller.signal, timeout: 130000,
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
      })
      if (disposed || gen !== generation) return null
      if (value?.ok === false) {
        error.value = value.message || '智能辅助暂不可用，可以继续使用普通搜索与手动匹配。'
        return null
      }
      result.value = value
      return value
    } catch (e) {
      if (!disposed && gen === generation) error.value = e.message || '请求失败，请稍后重试。'
      return null
    } finally {
      if (gen === generation) { busy.value = false; controller = null }
    }
  }
  onScopeDispose(() => { disposed = true; reset() })
  return { busy, result, error, run, reset }
}
