import { computed, onScopeDispose, ref, watch } from 'vue'

export const migrationKinds = { api: '接口', media: '媒体', image: '海报' }

export function safeHomepage(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '' }
  catch { return '' }
}

export function sourceMatchesTemplate(source, template) {
  const normalize = url => safeHomepage(url).replace(/\/$/, '')
  const endpoint = normalize(source.api_base_url)
  return !!endpoint && [template.api_base_url, ...(template.known_api_urls || [])].some(url => normalize(url) === endpoint)
}

export function sourceHostDraft(form) {
  return {
    api_base_url: form.api_base_url.trim(),
    allowed_hosts: [...new Set(form.hosts.split(/[\s,]+/).filter(Boolean))],
  }
}

export function useOnlineHostMigration({ api, sourceId, baseline, form, onConflict }) {
  const report = ref(null), selected = ref([]), pending = ref(false), error = ref('')
  const input = computed(() => sourceId.value && baseline.value
    ? JSON.stringify({ ...sourceHostDraft(form.value), expected_fingerprint: baseline.value }) : '')
  const blocked = computed(() => !!input.value && !report.value)
  const excluded = new Set()
  let timer, controller

  function stop() {
    clearTimeout(timer)
    controller?.abort()
    controller = null
  }

  function refresh(immediate = false) {
    stop()
    for (const change of report.value?.changes || []) {
      const key = JSON.stringify([change.old_host, change.new_host])
      if (selected.value.includes(change.old_host)) excluded.delete(key)
      else excluded.add(key)
    }
    report.value = null
    selected.value = []
    error.value = ''
    const body = input.value
    pending.value = !!body
    if (!body) { excluded.clear(); return }
    const path = '/api/online/sources/' + sourceId.value + '/host-migration-preview'
    async function read() {
      const current = new AbortController()
      controller = current
      try {
        const result = await api(path, { method: 'POST', body, signal: current.signal })
        if (current.signal.aborted) return
        const validHost = host => typeof host === 'string' && /^[a-z0-9.:-]{1,253}$/i.test(host)
        if (!/^[a-f0-9]{64}$/.test(result?.expected_fingerprint || '') || !Array.isArray(result.changes)
          || !result.changes.every(change => change && Object.hasOwn(migrationKinds, change.kind)
            && validHost(change.old_host) && validHost(change.new_host))) throw new Error()
        selected.value = result.changes.filter(change =>
          !excluded.has(JSON.stringify([change.old_host, change.new_host]))).map(change => change.old_host)
        report.value = result
      } catch (failure) {
        if (current.signal.aborted) return
        if (failure?.status === 409) onConflict()
        else error.value = failure?.status === 422 ? '请检查接口地址和允许主机的格式。' : '无法预览主机变更，请重试。'
      } finally {
        if (controller === current) { pending.value = false; controller = null }
      }
    }
    if (immediate) void read()
    else timer = setTimeout(read, 250)
  }

  watch(input, (_value, previous) => refresh(!previous), { flush: 'sync' })
  onScopeDispose(stop)
  return { report, selected, pending, error, blocked, retry: () => refresh(true) }
}
