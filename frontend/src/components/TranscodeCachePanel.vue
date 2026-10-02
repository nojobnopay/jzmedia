<template>
  <section class="card-block maintenance-action">
    <div><h3>播放缓存</h3>
    <p class="hint">清理全部媒体库的可回收转码与预缓存文件，正在播放的会话会保留。</p></div>
    <div class="bar">
      <button @click="preview" :disabled="busy">{{ busy ? '处理中…' : '检查可清理空间' }}</button>
    </div>
    <div v-if="candidate" class="settings-notice">
      <span>可释放 {{ fmtBytes(candidate.candidate_bytes) }}，共 {{ candidate.candidates }} 个缓存目录。清理后再次播放可能需要重新缓存。</span>
      <button class="danger" @click="clean" :disabled="busy">确认清理缓存</button>
      <button @click="candidate = null" :disabled="busy">取消</button>
    </div>
    <p v-if="message" class="feedback" role="status">{{ message }}</p>
  </section>
</template>
<script setup>
import { ref } from 'vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'

const busy = ref(false)
const candidate = ref(null)
const message = ref('')
async function preview() {
  busy.value = true
  candidate.value = null
  message.value = ''
  try {
    const d = await api('/api/stream/cache/clean', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
    if (d.candidates) candidate.value = d
    else message.value = `暂无可清理的缓存，当前占用 ${fmtBytes(d.total)}`
  } catch (e) { message.value = '检查失败：' + e.message }
  finally { busy.value = false }
}
async function clean() {
  if (!candidate.value || busy.value) return
  busy.value = true
  try {
    const d = await api('/api/stream/cache/clean', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
    candidate.value = null
    message.value = `已清理 ${d.removed} 个缓存目录，释放 ${fmtBytes(d.freed)}`
  } catch (e) { message.value = '清理失败：' + e.message }
  finally { busy.value = false }
}
</script>
