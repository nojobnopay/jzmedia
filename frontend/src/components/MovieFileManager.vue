<template>
          <details class="card-block files" open>
            <summary>文件与版本<span v-if="movie.version_count > 1">（共{{ movie.version_count }}个版本）</span></summary>
            <ul class="ver-list"><li v-for="v in movie.versions" :key="v.id" class="f-row">
              <span class="f-name">{{ baseName(v.file_path) }}<span v-if="v.edition">（{{ v.edition }}）</span><span v-if="v.spec">（{{ v.spec }}）</span></span>
              <span class="f-acts"><ActionMenu label="更多"><button @click="copyTvUrl(v)">复制直链</button></ActionMenu><a :href="blobUrl(v.file_path)" :download="baseName(v.file_path)">下载</a><button v-if="verBlocked[v.id]" disabled :title="verErr[v.id] || '无效文件'">无效</button><button v-else @click="emit('play', v)">播放</button><span v-if="verFriendly(v.id)" class="friendly-chip" title="浏览器可直播，几乎不占 NAS 算力">★</span><span v-else-if="verMethod[v.id]==='video_transcode'" class="trans-chip" title="浏览器需视频重编码，较耗 NAS 算力">转码</span></span>
            </li></ul>
            <div v-for="g in fileGroups" :key="g.key">
              <p v-if="g.items.length" class="hint">{{ g.label }}</p>
              <ul v-if="g.items.length">
                <li v-for="f in g.items" :key="g.key + f.name" class="f-row">
                  <span class="f-name">{{ f.name }}</span>
                  <span class="f-size">{{ fmtSize(f.size) }}</span>
                  <span class="f-acts">
                    <a :href="blobUrl(f.rel || f.name)" :download="baseName(f.rel || f.name)">下载</a>
                    <button v-if="isVideo(f.name)" @click="openPlayer(f)">播放</button>
                    <button v-else-if="pvKindOf(f.name)" @click="openPlayer(f)">预览</button>
                    <button v-if="delArm[f.rel || f.name] == null" @click="doFileDelete(f)">删除</button>
                    <button v-else @click="doFileDeleteConfirm(f)" class="danger">{{ (delArm[f.rel || f.name] || {}).requires_confirm ? '确认删除正片' : '确认删除' }}</button>
                  </span>
                </li>
              </ul>
            </div>
            <p v-if="delMsg" class="hint warn">{{ delMsg }}</p>
          </details>

          <p v-if="tvMsg" class="hint" role="status">{{ tvMsg }}</p>
          <input v-if="tvUrl" readonly :value="tvUrl" aria-label="媒体直链" class="copy-url" @focus="$event.target.select()" @click="$event.target.select()" />


    <div v-if="pvName" class="dlg-mask" @click.self="closePlayer">
      <div ref="pvDlgRef" class="dlg pv-dlg" role="dialog" aria-modal="true">
        <h3>{{ pvKind === 'video' ? '播放' : '预览' }}：{{ pvName }}</h3>
        <video v-if="pvKind === 'video'" :src="pvUrl" controls autoplay preload="metadata" class="pv-video" @error="pvErr = true"></video>
        <img v-else-if="pvKind === 'image'" :src="pvUrl" class="pv-img" />
        <iframe v-else-if="pvKind === 'pdf'" :src="pvUrl" class="pv-pdf"></iframe>
        <pre v-else-if="pvKind === 'text'" class="pv-text">{{ pvText }}</pre>
        <p v-if="pvErr" class="hint warn">文件为空或损坏，无法播放，请下载检查</p>
        <div class="bar"><a :href="pvUrl" :download="baseName(pvName)">下载原文件</a><button @click="togglePvFull">{{ pvFull ? '⤡ 退出全屏' : '⛶ 全屏' }}</button><button @click="closePlayer">关闭</button></div>
      </div>
    </div>

</template>

<script setup>
import ActionMenu from './ActionMenu.vue'

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { copyText } from '../clipboard.js'
import { useFocusTrap } from '../useFocusTrap.js'

// 详情页文件管理器（评审 R05-Q4：自 Detail.vue 抽出）：版本/花絮/字幕/NFO 列表、预览、
// 删除（正片二次确认）、电视直链。播放交给父页（PlayerModal），删除后发 changed 让父页重载。
const props = defineProps({
  movieId: { type: Number, required: true },
  movie: { type: Object, required: true },
  sideFiles: { type: Object, default: null },
  verBlocked: { type: Object, default: () => ({}) },
  verErr: { type: Object, default: () => ({}) },
  verMethod: { type: Object, default: () => ({}) },
  verFriendly: { type: Function, default: () => false }
})
const emit = defineEmits(['play', 'changed'])
const pvDlgRef = ref(null)

const tvMsg = ref('')
const tvUrl = ref('')
async function copyTvUrl(v) {
  tvMsg.value = ''
  tvUrl.value = ''
  const url = location.origin + `/api/movies/${v.id}/blob?name=${encodeURIComponent(v.file_path)}`
  if (await copyText(url)) {
    tvMsg.value = '已复制，在 Kodi 里打开该链接即播'
  } else {
    tvUrl.value = url
    tvMsg.value = '自动复制失败（浏览器限制），已显示链接，点框后 Ctrl+C 手动复制'
  }
}

const fileGroups = computed(() => {
  const s = props.sideFiles || {}
  return [
    { key: 'extras', label: '🎬 花絮', items: s.extras || [] },
    { key: 'samples', label: '🎞 样片', items: s.samples || [] },
    { key: 'subtitles', label: '💬 字幕', items: s.subtitles || [] },
    { key: 'nfos', label: 'NFO', items: s.nfos || [] },
    { key: 'others', label: '周边（音乐/海报/剧本等）', items: s.others || [] },
  ]
})

function baseName(p) {
  return String(p || '').split('/').pop()
}
function fmtSize(n) {
  n = Number(n) || 0
  if (n < 1024) return n + 'B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'M'
  return (n / 1024 / 1024 / 1024).toFixed(2) + 'G'
}
function blobUrl(name) {
  return `/api/movies/${props.movieId}/blob?name=${encodeURIComponent(name)}`
}

const pvName = ref('')
const pvUrl = ref('')
const pvKind = ref('')
const pvText = ref('')
const pvErr = ref(false)
function pvKindOf(name) {
  const ex = ('.' + String(name || '').split('.').pop()).toLowerCase()
  if (['.mp4', '.mkv', '.webm', '.mov', '.avi', '.ts', '.m2ts', '.flv'].includes(ex)) return 'video'
  if (['.jpg', '.jpeg', '.png', '.webp', '.gif'].includes(ex)) return 'image'
  if (ex === '.pdf') return 'pdf'
  if (['.txt', '.srt', '.ass', '.ssa', '.lrc', '.nfo', '.md'].includes(ex)) return 'text'
  return ''
}
function isVideo(name) {
  return pvKindOf(name) === 'video'
}
async function openPlayer(f) {
  const name = f.rel || f.name
  pvErr.value = false
  pvName.value = f.name
  pvKind.value = pvKindOf(f.name)
  // 图片/PDF 走 inline（评审 B6/R05-D3：attachment 会让 iframe 变下载）
  pvUrl.value = blobUrl(name) + ((pvKind.value === 'image' || pvKind.value === 'pdf') ? '&inline=1' : '')
  pvText.value = ''
  if (pvKind.value === 'text') {
    try {
      const r = await fetch(`/api/movies/${props.movieId}/blob?name=${encodeURIComponent(name)}&mode=text`)
      pvText.value = await r.text()
    } catch (e) {
      pvText.value = '预览失败：' + e.message
    }
  }
}
function closePlayer() {
  // 弹窗 v-if 卸载 <video> 即停播；全屏中卸载会自动退出全屏
  pvName.value = ''
  pvUrl.value = ''
  pvKind.value = ''
  pvText.value = ''
  pvErr.value = false
}

// 预览全屏（对齐 PlayerModal 做法：对话框元素全屏 + fullscreenchange 同步）
const pvFull = ref(false)
function onFullscreenChange() {
  // 只认本预览对话框（播放器全屏同页，不能跟着翻按钮文案）
  pvFull.value = document.fullscreenElement === pvDlgRef.value
}
async function togglePvFull() {
  const el = pvDlgRef.value
  try {
    if (document.fullscreenElement === el) await document.exitFullscreen()
    else if (el && el.requestFullscreen) await el.requestFullscreen()
  } catch (e) { /* 浏览器拒绝（无用户手势等）：保持窗口态 */ }
}

const delArm = ref({})
const delMsg = ref('')
// 海报大图：先展本地 w500（即时），后台拉原图成功后替换；失败静默保持

function onKeydown(e) {
  // 全屏中 Esc 交给浏览器退全屏，不关弹窗（与播放器一致）
  if (e.key === 'Escape' && pvName.value && !document.fullscreenElement) closePlayer()
}
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('fullscreenchange', onFullscreenChange)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
})
useFocusTrap(computed(() => !!pvName.value), pvDlgRef)

async function doFileDelete(f) {
  const name = f.rel || f.name
  delMsg.value = ''
  try {
    const d = await api('/api/movies/' + props.movieId + '/files', {
      method: 'DELETE',
      body: JSON.stringify({ name, dry_run: true })
    })
    const p = (d.plans || [])[0] || {}
    // 两步确认对所有文件统一（评审 B6/R05-D4）：非正片单击即删太容易误触
    delArm.value = { ...delArm.value, [name]: p }
    delMsg.value = p.requires_confirm
      ? `警告：将删除正片 ${f.name}，海报墙同步移除。再点「确认删除正片」执行`
      : `将删除 ${f.name}，再点「确认删除」执行`
  } catch (e) {
    delMsg.value = '删除失败：' + e.message
  }
}
async function doFileDeleteConfirm(f) {
  const name = f.rel || f.name
  try {
    const d = await api('/api/movies/' + props.movieId + '/files', {
      method: 'DELETE',
      body: JSON.stringify({ name, dry_run: false, confirm: true })
    })
    const r = (d.results || [])[0] || {}
    delMsg.value = r.status === 'deleted' ? '已删除，库已同步清理' : ('删除：' + (r.status || '失败'))
    const cp = { ...delArm.value }
    delete cp[name]
    delArm.value = cp
    closePlayer()
    emit('changed')
  } catch (e) {
    delMsg.value = '删除失败：' + e.message
  }
}

</script>

<style scoped>
/* 自 Detail.vue 迁入的原始样式（评审 R05-Q4，保持观感一致） */
.friendly-chip { color: #7ed321; font-size: 0.8125rem; }
.trans-chip { color: #e0a63c; font-size: 0.75rem; border: 1px dashed #6e5426; border-radius: 999px; padding: 0 8px; }
.tvplay summary { cursor: pointer; color: #ccc; font-size: 0.9375rem; }
.tvplay .hint { color: #888; font-size: 0.8125rem; overflow-wrap: anywhere; }
.copy-url { display: block; width: 100%; box-sizing: border-box; margin: 6px 0 2px; padding: 6px 8px;
  background: #141414; border: 1px dashed #444; border-radius: 6px; color: #bbb;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.75rem;
  overflow-x: auto; white-space: nowrap; }
.files summary { cursor: pointer; color: #ccc; font-size: 0.9375rem; }
.files ul { color: #888; font-size: 0.875rem; }
.files a { color: #6ab0ff; margin-left: 6px; }
.f-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 3px 0; }
.f-name { flex: 1; min-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.f-size { color: #666; font-size: 0.75rem; }
.f-acts { display: flex; gap: 8px; align-items: center; margin-left: auto; }
.ver-list { list-style: none; margin: 4px 0; padding: 0; }
.pv-video { width: 100%; max-height: 78vh; background: #000; border-radius: 8px; }
.pv-img { max-width: 100%; max-height: 78vh; object-fit: contain; border-radius: 8px; }
.pv-pdf { width: 100%; height: 80vh; border: none; border-radius: 8px; background: #fff; }
.pv-text { white-space: pre-wrap; max-height: 70vh; overflow: auto; background: #111; padding: 10px; border-radius: 8px; color: #ccc; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.hint.warn { color: #e0a63c; }
/* 弹窗基底（与 Detail 其余弹窗一致） */
.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.66); display: flex; align-items: center; justify-content: center; z-index: 50; }
.dlg { background: #1c1c1c; border-radius: 10px; padding: 16px; max-width: 860px; width: calc(100vw - 48px); max-height: 88vh; overflow: auto; }
/* 预览弹窗：跟随屏幕分辨率（大屏不再固定 860px），并支持全屏 */
.pv-dlg { width: min(1600px, calc(100vw - 48px)); max-height: 92vh; }
.pv-dlg:fullscreen { width: 100vw; height: 100vh; max-width: none; max-height: none; border-radius: 0; padding: 12px 16px; display: flex; flex-direction: column; }
.pv-dlg:fullscreen h3 { flex: none; margin: 0 0 8px; }
.pv-dlg:fullscreen .bar { flex: none; }
.pv-dlg:fullscreen .pv-video,
.pv-dlg:fullscreen .pv-pdf,
.pv-dlg:fullscreen .pv-text { flex: 1; min-height: 0; height: auto; max-height: none; }
.pv-dlg:fullscreen .pv-img { flex: 1; min-height: 0; width: 100%; height: 100%; max-width: 100%; max-height: none; margin: 0 auto; object-fit: contain; }
</style>
