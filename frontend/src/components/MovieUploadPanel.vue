<template>
  <section class="card-block">
    <h3>上传文件 <JzButton variant="ghost" icon-only class="q-tip" aria-label="上传说明" aria-describedby="movie-upload-hint" @keydown.esc="$event.currentTarget.blur()"><AppIcon name="help" :size="18" /><span id="movie-upload-hint" class="q-bubble" role="tooltip">适合上传：海报/剧照（jpg/png）、音乐/原声（mp3/flac）、剧本/字幕（txt/srt/ass/pdf）、花絮视频（自动进 extras/）；正片新版本视频也可上传，会自动刮削入库。&gt;2GB 建议在局域网操作，可随时取消。</span></JzButton></h3>
    <div class="bar up-row">
      <input type="file" ref="upInput" :disabled="!!upCtl" />
      <select v-model="upSubdir" :disabled="!!upCtl">
        <option value="">片目录</option>
        <option value="extras">extras/</option>
      </select>
      <JzButton @click="doUpload" :disabled="!!upCtl" type="button" icon="upload">上传</JzButton>
      <JzButton v-if="upCtl" @click="cancelUpload" type="button">取消</JzButton>
      <span v-if="upPct !== null">{{ upPct }}%</span>
      <span v-if="upScanning" class="up-scan">已传完，正在联网匹配 TMDB 元数据并下载海报，请耐心等待（已等待 {{ upScanSecs }}s）</span>
      <span v-else>{{ upMsg }}</span>
    </div>
    <div v-if="upPct !== null" class="up-bar"><i :style="{ width: upPct + '%' }"></i></div>
  </section>
</template>

<script setup>
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'

import { computed, onUnmounted, ref } from 'vue'
import { apiUpload } from '../api.js'
import { requestProtection } from '../viewing.js'

// 详情页上传面板（评审 R05-Q4：自 Detail.vue 抽出）：单文件 + 可放片目录/extras，
// 传完服务端同步刮削，前端给“刮削中”等待提示。上传成功发 uploaded 让父页刷新。
const props = defineProps({ movieId: { type: Number, required: true } })
const emit = defineEmits(['uploaded'])

const upInput = ref(null)
const upSubdir = ref('')
const upPct = ref(null)
const upMsg = ref('')
const upScanning = ref(false)
const upScanSecs = ref(0)
const upAbort = ref(null)
let upScanTimer = null
let upHintTimer = null
let upHideTimer = null
let disposed = false
const upCtl = computed(() => !!upAbort.value)

function stopUpScanTicker() {
  if (upScanTimer) { clearInterval(upScanTimer); upScanTimer = null }
  if (upHintTimer) { clearTimeout(upHintTimer); upHintTimer = null }
  if (upHideTimer) { clearTimeout(upHideTimer); upHideTimer = null }
}

async function doUpload() {
  if (disposed || upCtl.value) return
  const id = props.movieId
  const current = () => !disposed && id === props.movieId
  const files = upInput.value && upInput.value.files
  if (!files || !files.length) {
    upMsg.value = '先选文件'
    return
  }
  const file = files[0]
  stopUpScanTicker()
  upMsg.value = ''
  upPct.value = 0
  upScanning.value = false
  upScanSecs.value = 0
  const h = apiUpload(`/api/movies/${id}/upload`, file, {
    authorize: async () => current() && await requestProtection('unlock') && current(),
    canStart: current,
    subdir: upSubdir.value,
    onProgress: (p) => { if (current()) upPct.value = p },
    onUploaded: () => {
      if (!current()) return
      // 延迟 800ms 再切“刮削中”，字幕/花絮等本地快路径不会闪提示
      if (upHintTimer) clearTimeout(upHintTimer)
      upHintTimer = setTimeout(() => {
        if (!current() || !upAbort.value) return
        upScanning.value = true
        upScanSecs.value = 0
        if (upScanTimer) clearInterval(upScanTimer)
        upScanTimer = setInterval(() => { upScanSecs.value++ }, 1000)
      }, 800)
    }
  })
  upAbort.value = h.abort
  try {
    const r = await h.promise
    if (!current()) return
    stopUpScanTicker()
    upScanning.value = false
    upPct.value = 100
    upMsg.value = `已上传 ${file.name}` + (r && r.status && r.status !== 'stored' ? `（${r.status}）` : '')
    if (upInput.value) upInput.value.value = ''
    emit('uploaded')
  } catch (e) {
    if (!current()) return
    stopUpScanTicker()
    upScanning.value = false
    upMsg.value = '上传失败：' + e.message
  } finally {
    if (current()) {
      upAbort.value = null
      upHideTimer = setTimeout(() => { if (current() && !upAbort.value) { upPct.value = null; upScanning.value = false } }, 3000)
    }
  }
}
function cancelUpload() {
  upAbort.value?.()
}
onUnmounted(() => { disposed = true; stopUpScanTicker(); upAbort.value = null })
</script>

<style scoped>
/* 与详情页其它卡片一致的局部样式（R14-Q5 约定：静态样式随组件，动态元素才进全局） */
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.up-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 0; }
.up-bar { height: 6px; background: #262626; border-radius: 3px; overflow: hidden; margin: 4px 0; }
.up-bar i { display: block; height: 100%; background: var(--jz-accent); }
.up-scan { color: #e0a63c; font-size: 0.8125rem; }
.card-block { position: relative; }
.q-tip { vertical-align: middle; }
.q-tip .q-bubble { display: none; position: absolute; left: 12px; right: 12px; top: 52px; max-width: 320px; background: var(--jz-surface-3); border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-m); padding: 10px 12px; color: var(--jz-text); font-size: var(--jz-font-s); line-height: 1.7; z-index: 30; white-space: normal; text-align: left; }
.q-tip:hover .q-bubble, .q-tip:focus-within .q-bubble { display: block; }
</style>
