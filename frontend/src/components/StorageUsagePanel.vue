<template>
  <section class="card-block storage-usage-panel">
    <div class="section-heading"><div><h3>应用数据与缓存</h3><p class="hint">规划此服务的全部应用数据；不包含原始媒体文件。</p></div>
      <JzButton @click="refresh(true)" :loading="loading" :disabled="saving" type="button" icon="refresh">刷新占用</JzButton></div>
    <p v-if="usage?.ok" class="storage-total">当前已用 <strong>{{ fmtBytes(usage.total_bytes) }}</strong></p>
    <p v-if="loading && !usage" class="hint" role="status">正在统计应用数据…</p>
    <p v-if="error || usage && !usage.ok" class="warn-text" role="alert">{{ error || usage.error || '部分应用数据暂时无法统计' }}
      <JzButton @click="refresh(true)" :disabled="loading || saving" type="button" icon="refresh">重试统计</JzButton></p>
    <p v-if="usage?.over_limit" class="warn-text" role="alert">实际占用已达到 {{ fmtBytes(usage.warning_bytes) }} 总预算，闲时缩略图生成已暂停。可重新分配容量或清理缓存。</p>
    <CacheSettingsPanel :usage="usage" :usage-error="error" :usage-loading="loading" @saving="saving = $event" @refresh="refresh(true)" />
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import JzButton from './JzButton.vue'
import CacheSettingsPanel from './CacheSettingsPanel.vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'

const usage = ref(null), loading = ref(false), saving = ref(false), error = ref('')
let generation = 0
let request = null
async function refresh(force = false) {
  const current = ++generation
  request?.abort()
  request = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const result = await api(force ? '/api/data-usage?refresh=true' : '/api/data-usage', { signal: request.signal })
    if (current === generation) usage.value = result
  } catch (e) { if (current === generation) error.value = '空间统计失败：' + e.message }
  finally { if (current === generation) loading.value = false }
}
function onUsageChanged() { refresh() }
onMounted(() => { refresh(); window.addEventListener('jzmedia:data-usage-changed', onUsageChanged) })
onBeforeUnmount(() => { generation++; request?.abort(); window.removeEventListener('jzmedia:data-usage-changed', onUsageChanged) })
</script>

<style scoped>
.storage-total { margin: 0 0 20px; color: var(--jz-text-dim); font-size: var(--jz-font-m); }
.storage-total strong { margin-left: 8px; font-size: var(--jz-font-xl); color: var(--jz-text); }
</style>
