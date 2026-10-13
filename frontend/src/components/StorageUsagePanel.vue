<template>
  <section class="card-block storage-usage-panel">
    <div class="section-heading"><div><h3>应用数据占用</h3><p class="hint">统计数据库、图片、进度缩略图和播放缓存；不包含原始媒体文件。</p></div><JzButton @click="refresh(true)" :disabled="busy" type="button" icon="refresh">刷新占用</JzButton></div>
    <template v-if="usage?.ok">
      <p class="storage-total"><strong>{{ fmtBytes(usage.total_bytes) }}</strong> / 20 GB 提醒线</p>
      <progress :value="Math.min(usage.total_bytes, usage.warning_bytes)" :max="usage.warning_bytes" aria-label="应用数据占用比例" />
      <p v-if="usage.over_limit" class="warn-text" role="alert">已达到提醒线。自动生成进度缩略图会暂停；请检查下面可清理的缓存。</p>
      <dl class="info-grid"><dt>数据库与写入日志</dt><dd>{{ fmtBytes(usage.categories.database) }}</dd><dt>海报与图片</dt><dd>{{ fmtBytes(usage.categories.posters) }}</dd><dt>进度缩略图</dt><dd>{{ fmtBytes(usage.categories.previews) }}</dd><dt>播放缓存</dt><dd>{{ fmtBytes(usage.categories.transcode) }}</dd><dt>其他应用数据</dt><dd>{{ fmtBytes(usage.categories.other) }}</dd></dl>
    </template>
    <p v-else class="hint" role="status">{{ error || usage?.error || '正在统计应用数据…' }}</p>
    <div class="preview-clean"><div><h4>进度缩略图</h4><p class="hint">清理后可释放空间；下次需要预览时须重新生成。自动队列中的新片会在空闲时继续处理。</p></div>
      <div class="bar"><JzButton @click="checkPreviewClean" :disabled="busy" type="button" icon="eye">检查可清理空间</JzButton></div>
      <div v-if="candidate" class="settings-notice"><span>可清理 {{ candidate.candidates }} 个缩略图目录，释放约 {{ fmtBytes(candidate.candidate_bytes) }}。</span><JzButton @click="cleanPreviews" :disabled="busy" type="button" variant="danger">确认清理缩略图</JzButton><JzButton @click="candidate = null" :disabled="busy" type="button">取消</JzButton></div>
      <p v-if="message" class="feedback" role="status">{{ message }}</p>
    </div>
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import JzButton from './JzButton.vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'

const usage = ref(null)
const candidate = ref(null)
const busy = ref(false)
const error = ref('')
const message = ref('')

async function refresh(force = false) {
  error.value = ''
  try { usage.value = await api(force ? '/api/data-usage?refresh=true' : '/api/data-usage') }
  catch (e) { error.value = '空间统计失败：' + e.message }
}
async function checkPreviewClean() {
  busy.value = true
  candidate.value = null
  message.value = ''
  try {
    const result = await api('/api/stream/previews/cache/clean', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
    if (result.candidates) candidate.value = result
    else message.value = '暂无可清理的进度缩略图'
  } catch (e) { message.value = '检查失败：' + e.message }
  finally { busy.value = false }
}
async function cleanPreviews() {
  if (!candidate.value || busy.value) return
  busy.value = true
  try {
    const result = await api('/api/stream/previews/cache/clean', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
    candidate.value = null
    message.value = `已清理 ${result.removed} 个目录，释放 ${fmtBytes(result.freed)}`
    window.dispatchEvent(new Event('jzmedia:data-usage-changed'))
  } catch (e) { message.value = '清理失败：' + e.message }
  finally { busy.value = false }
}
function onUsageChanged() { refresh() }
onMounted(() => { refresh(); window.addEventListener('jzmedia:data-usage-changed', onUsageChanged) })
onBeforeUnmount(() => window.removeEventListener('jzmedia:data-usage-changed', onUsageChanged))
</script>

<style scoped>
.storage-total { margin: 0 0 8px; font-size: var(--jz-font-m); }
.storage-total strong { font-size: var(--jz-font-xl); }
progress { display: block; width: 100%; max-width: 36rem; height: 8px; margin-bottom: 16px; }
.preview-clean { margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--jz-border); }
.preview-clean h4 { margin: 0 0 8px; font-size: var(--jz-font-m); }
</style>
