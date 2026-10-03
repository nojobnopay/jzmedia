<template>
  <Teleport to="body">
    <div v-if="currentFile" class="file-preview-mask" @click.self="requestClose" @keydown.stop>
      <section ref="dialog" class="file-preview-dialog" role="dialog" aria-modal="true" :aria-label="'文件预览：' + currentFile.name">
        <header><div><span class="file-preview-eyebrow">文件预览</span><h3>{{ currentFile.name }}</h3></div><JzButton variant="ghost" icon-only class="file-preview-close" aria-label="关闭文件预览" @click="requestClose"><AppIcon name="close" /></JzButton></header>
        <p v-if="currentFile.rel" class="file-preview-path">{{ currentFile.rel }}<span v-if="currentFile.size != null"> · {{ fmtBytes(currentFile.size) }}</span></p>
        <div class="file-preview-content" :aria-busy="loading">
          <p v-if="loading" role="status">正在读取文件…</p>
          <video v-else-if="kind === 'video' && !error" :key="generation" ref="media" :src="inlineUrl" autoplay controls playsinline preload="metadata" @error="assetError($event, '浏览器无法播放此原文件。可下载后播放，或扫描入库后使用完整播放器。')"></video>
          <img v-else-if="kind === 'image' && !error" :key="generation" ref="media" :src="inlineUrl" :alt="currentFile.name" @error="assetError($event, '图片无法显示，可重试或下载原文件检查。')" />
          <iframe v-else-if="kind === 'pdf' && !error" :key="generation" ref="media" :src="inlineUrl" :title="currentFile.name" @error="assetError($event, 'PDF 无法显示，可下载原文件查看。')"></iframe>
          <pre v-else-if="kind === 'text' && !error" class="file-preview-text">{{ text || '（空文件）' }}</pre>
          <div v-else-if="kind === 'unknown' && !error" class="file-preview-unavailable"><strong>此格式暂不支持预览</strong><p>可下载原文件后查看。</p></div>
          <p v-if="error" class="file-preview-error" role="alert">{{ error }}</p>
        </div>
        <p v-if="kind === 'video' && !error" class="file-preview-hint">原文件预览；浏览器不兼容时，可下载或扫描入库后使用完整播放器。</p>
        <p v-if="kind === 'pdf' && !error" class="file-preview-hint">若浏览器未显示 PDF，可下载原文件查看。</p>
        <p v-if="kind === 'text' && !error" class="file-preview-hint">{{ truncated || currentFile.size > limit ? `仅预览前 ${Math.round(limit / 1024)} KB；下载可查看完整内容。` : '以纯文本显示文件内容。' }}</p>
        <footer><a :href="currentFile.url" :download="currentFile.name"><AppIcon name="download" :size="16" />下载原文件</a><JzButton icon="refresh" v-if="error" @click="retry">重试预览</JzButton><JzButton :icon="fullscreen ? 'exit-fullscreen' : 'fullscreen'" v-if="kind !== 'unknown'" @click="toggleFullscreen">{{ fullscreen ? '退出全屏' : '全屏预览' }}</JzButton><JzButton @click="requestClose">关闭</JzButton></footer>
        <p v-if="fullscreenError" class="file-preview-error" role="status">{{ fullscreenError }}</p>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, onUnmounted, ref, watch } from 'vue'
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'
import { fmtBytes } from '../format.js'
import { clearFilePreviewElement, filePreviewUrl } from '../filePreview.js'
import { useFilePreview } from '../useFilePreview.js'
import { useFocusTrap } from '../useFocusTrap.js'

const props = defineProps({ file: { type: Object, required: true } })
const emit = defineEmits(['close'])
const dialog = ref(null)
const media = ref(null)
const fullscreen = ref(false)
const fullscreenError = ref('')
const state = useFilePreview({ clearMedia: () => clearFilePreviewElement(media.value) })
const { file: currentFile, kind, text, error, loading, truncated, limit, generation } = state
const inlineUrl = computed(() => currentFile.value ? filePreviewUrl(currentFile.value.url, 'inline') : '')
watch(() => props.file, file => state.open(file), { immediate: true, flush: 'sync' })
useFocusTrap(computed(() => !!currentFile.value), dialog)
function assetError(event, message) {
  if (event.currentTarget !== media.value || !currentFile.value) return
  clearFilePreviewElement(media.value)
  state.failAsset(message)
}
function retry() { fullscreenError.value = ''; state.open(props.file) }
function onFullscreenChange() { fullscreen.value = document.fullscreenElement === dialog.value }
async function toggleFullscreen() {
  fullscreenError.value = ''
  try {
    if (document.fullscreenElement === dialog.value) await document.exitFullscreen()
    else if (dialog.value?.requestFullscreen) await dialog.value.requestFullscreen()
    else fullscreenError.value = '此浏览器暂不支持全屏预览。'
  } catch (cause) { fullscreenError.value = '未能进入全屏：' + cause.message }
}
function requestClose() { state.close(); emit('close') }
function onKeydown(event) {
  if (event.key !== 'Escape' || !currentFile.value || document.fullscreenElement) return
  event.preventDefault()
  event.stopPropagation()
  requestClose()
}
onMounted(() => { document.addEventListener('keydown', onKeydown, true); document.addEventListener('fullscreenchange', onFullscreenChange) })
onBeforeUnmount(() => state.dispose())
onUnmounted(() => { document.removeEventListener('keydown', onKeydown, true); document.removeEventListener('fullscreenchange', onFullscreenChange) })
</script>

<style scoped>
.file-preview-mask { position: fixed; inset: 0; z-index: var(--jz-z-preview); background: var(--jz-overlay); display: flex; align-items: center; justify-content: center; padding: 20px; }
.file-preview-dialog { display: flex; flex-direction: column; box-sizing: border-box; width: min(1380px, 100%); max-height: 94dvh; padding: 24px; border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-dialog); background: var(--jz-surface); color: var(--jz-text); box-shadow: var(--jz-shadow-dialog); overflow: auto; }
.file-preview-dialog header { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
.file-preview-dialog header > div { min-width: 0; }
.file-preview-eyebrow { color: var(--jz-text-dim); font-size: .75rem; }
.file-preview-dialog h3 { font-size: var(--jz-font-xl); line-height: 1.5; margin: 4px 0 8px; overflow-wrap: anywhere; }
.file-preview-close { flex: none; }
.file-preview-path { font-size: .78rem; color: var(--jz-text-dim); margin: 0 0 12px; overflow-wrap: anywhere; }
.file-preview-content { flex: 1; min-height: 0; overflow: auto; display: flex; align-items: center; justify-content: center; background: var(--jz-bg); border-radius: var(--jz-radius-s); }
.file-preview-content video { width: 100%; max-height: 72dvh; }
.file-preview-content img { display: block; max-width: 100%; max-height: 72dvh; object-fit: contain; }
.file-preview-content iframe { width: 100%; height: 72dvh; border: 0; background: var(--jz-surface-2); }
.file-preview-text { align-self: stretch; flex: 1; padding: 16px; margin: 0; min-height: 180px; max-height: 68dvh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--jz-text); font-size: .86rem; line-height: 1.65; }
.file-preview-unavailable { text-align: center; padding: 48px 24px; }
.file-preview-hint { font-size: .78rem; color: var(--jz-text-dim); margin: 12px 0 0; }
.file-preview-error { color: var(--jz-warn); padding: 18px; overflow-wrap: anywhere; }
.file-preview-dialog footer { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 16px; }
.file-preview-dialog footer a { color: var(--jz-link); margin-right: auto; }
.file-preview-dialog:fullscreen { width: 100vw; height: 100dvh; max-height: none; border: 0; border-radius: 0; }
.file-preview-dialog:fullscreen .file-preview-content video, .file-preview-dialog:fullscreen .file-preview-content img { max-height: 100%; }
.file-preview-dialog:fullscreen .file-preview-content iframe, .file-preview-dialog:fullscreen .file-preview-text { height: 100%; max-height: none; }
@media (max-width: 600px) { .file-preview-mask { padding: 8px; } .file-preview-dialog { padding: 12px; } .file-preview-dialog footer { gap: 8px; } .file-preview-dialog footer a { width: 100%; } }
</style>
