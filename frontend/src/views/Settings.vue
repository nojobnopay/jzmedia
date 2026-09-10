<template>
  <div class="page">
    <h2>设置</h2>
    <p v-if="s">媒体目录：{{ s.media_root }} · 语言：{{ s.tmdb_language }} · TMDB Token：{{ s.tmdb_configured ? '已配' : '未配' }} · 图片源：{{ s.tmdb_image_base }}</p>
    <h3>文件整理</h3>
    <div class="bar"><button @click="loadPreview">预览</button><button @click="doRename">执行整理</button></div>
    <pre>{{ JSON.stringify(plans, null, 1) }}</pre>
    <p>{{ msg }}</p>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const s = ref(null)
const plans = ref([])
const msg = ref('')

async function loadPreview() {
  const d = await api('/api/files/preview')
  plans.value = d.plans
}
async function doRename() {
  msg.value = ''
  const d = await api('/api/files/rename', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
  plans.value = d.results
  msg.value = '执行完毕'
}
onMounted(async () => {
  s.value = await api('/api/settings')
  await loadPreview()
})
</script>
