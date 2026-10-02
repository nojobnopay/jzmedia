import { onScopeDispose, ref } from 'vue'
import { api } from './api.js'

export function useFileChanges(requestApi = api) {
  const changes = ref({})
  const errors = ref({})
  const requests = new Map()
  const controllers = new Map()
  let disposed = false
  async function refresh(libraryId, { fresh = false } = {}) {
    const id = Number(libraryId)
    if (!id || disposed) return null
    if (requests.has(id)) {
      if (!fresh) return requests.get(id)
      await requests.get(id)
      if (disposed) return null
      return refresh(id)
    }
    const controller = new AbortController()
    controllers.set(id, controller)
    const request = (async () => {
      try {
        const result = await requestApi('/api/fs/changes?library=' + id, { signal: controller.signal })
        if (disposed) return null
        changes.value = { ...changes.value, [id]: result }
        errors.value = { ...errors.value, [id]: '' }
        return result
      } catch (e) {
        if (!disposed) errors.value = { ...errors.value, [id]: '暂时无法读取文件变更：' + e.message }
        return null
      } finally { requests.delete(id); controllers.delete(id) }
    })()
    requests.set(id, request)
    return request
  }
  onScopeDispose(() => { disposed = true; controllers.forEach(controller => controller.abort()) })
  return { changes, errors, refresh }
}
