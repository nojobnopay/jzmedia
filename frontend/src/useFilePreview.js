import { ref } from 'vue'
import { filePreviewKind, filePreviewReadError, filePreviewUrl } from './filePreview.js'

// Text fetches may outlive a dialog or ignore abort (e.g. a cached response body).
// Every await is guarded by a generation, including Response.text().
export function useFilePreview({ request = (...args) => fetch(...args), clearMedia = () => {} } = {}) {
  const file = ref(null)
  const kind = ref('unknown')
  const text = ref('')
  const error = ref('')
  const loading = ref(false)
  const truncated = ref(false)
  const limit = ref(65536)
  const generation = ref(0)
  let controller = null
  let disposed = false

  function close() {
    generation.value++
    controller?.abort()
    controller = null
    clearMedia()
    file.value = null
    text.value = ''
    error.value = ''
    loading.value = false
    truncated.value = false
  }
  async function probeFile(item, probe) {
    try {
      // Discard the body even when the server ignores Range and replies with 200.
      const response = await request(item.url, { headers: { Range: 'bytes=0-0' }, signal: probe.signal })
      const result = { ok: response.ok, status: response.status }
      await response.body?.cancel()
      return result
    } finally { probe.abort() }
  }
  async function open(item) {
    close()
    if (disposed || !item) return
    file.value = { ...item }
    kind.value = filePreviewKind(item.name || item.rel)
    const openingKind = kind.value
    if (!['text', 'pdf'].includes(openingKind)) return
    const mine = generation.value
    const active = () => !disposed && mine === generation.value
    controller = new AbortController()
    loading.value = true
    try {
      if (openingKind === 'pdf') {
        // Iframe error events do not expose HTTP failures. Only attach its src
        // after the small status request confirms that the file is readable.
        const result = await probeFile(item, controller)
        if (active() && !result.ok) error.value = filePreviewReadError(result.status)
        return
      }
      const response = await request(filePreviewUrl(item.url, 'text'), { signal: controller.signal })
      if (!active()) return
      if (!response.ok) throw new Error(filePreviewReadError(response.status))
      const value = await response.text()
      if (!active()) return
      text.value = value
      truncated.value = response.headers?.get('X-Preview-Truncated') === 'true'
      limit.value = Number(response.headers?.get('X-Preview-Limit')) || 65536
    } catch (cause) {
      if (active()) error.value = openingKind === 'pdf' ? '暂时无法读取 PDF，请检查连接后重试。' : cause.message || '文件预览失败'
    } finally {
      if (active()) loading.value = false
    }
  }
  async function failAsset(fallback) {
    const item = file.value
    if (!item || disposed) return
    const mine = generation.value
    const active = () => !disposed && mine === generation.value
    error.value = fallback
    controller?.abort()
    const probe = new AbortController()
    controller = probe
    try {
      const result = await probeFile(item, probe)
      if (active() && !result.ok) error.value = filePreviewReadError(result.status)
    } catch (cause) {
      if (active()) error.value = '暂时无法读取文件，请检查连接后重试。'
    }
  }
  function dispose() { disposed = true; close() }
  return { file, kind, text, error, loading, truncated, limit, generation, open, close, failAsset, dispose }
}
