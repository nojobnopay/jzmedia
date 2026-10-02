<template>
  <section>
    <h2>设置视频库</h2>
    <p>先选择文件存放位置，再指定其中的电影或剧集目录。一个存储位置可以包含多个视频库。</p>
    <fieldset :disabled="working">
      <label>这次添加什么内容？
        <select v-model="kind"><option value="movie">电影</option><option value="tv">剧集</option></select>
      </label>
      <div class="choices">
        <label><input v-model="mode" type="radio" value="existing" /> 使用已有视频库</label>
        <label><input v-model="mode" type="radio" value="video" /> 在已有位置添加视频库</label>
        <label><input v-model="mode" type="radio" value="media" /> 添加文件存放位置</label>
      </div>
      <template v-if="mode === 'existing'">
        <label v-if="candidates.length">目标视频库
          <select v-model.number="selected" @change="edit = false; message = ''">
            <option v-for="lib in candidates" :key="lib.id" :value="lib.id">{{ lib.media_name }} / {{ lib.name }} · {{ lib.kind === 'tv' ? '剧集' : '电影' }}{{ lib.kind !== kind ? '（空库，可调整类型）' : '' }}</option>
          </select>
        </label>
        <p v-else>没有可用的{{ kind === 'tv' ? '剧集' : '电影' }}库，请添加视频库或文件存放位置。已停用的库需先在设置页启用。</p>
        <template v-if="target">
          <dl><dt>存储位置</dt><dd>{{ media?.name }} · {{ media?.source }}</dd><dt>根路径</dt><dd>{{ media?.source === 'local' ? media.path : media?.source === 'smb' ? (media.smb_host + '/' + media.smb_share + '/' + (media.smb_subpath || '')) : media?.nfs_export }}</dd><dt>视频子目录</dt><dd>{{ target.subpath || '使用根目录' }}</dd></dl>
          <p class="hint">请确认这里指向真实媒体目录。首次启动自带的默认库也需要检查。</p>
          <p v-if="target.read_only">此库只读：可以扫描和播放，无法上传。</p>
          <p v-if="target.kind !== kind">这是空的{{ target.kind === 'tv' ? '剧集' : '电影' }}库，保存下面的类型调整后即可继续。</p>
          <VideoLibraryForm v-if="edit || target.kind !== kind" :key="target.id + ':' + kind"
            :library="{ ...target, kind }" @saved="videoSaved" @cancel="edit = false" @busy="childBusy = $event" />
          <div v-else class="bar"><button @click="edit = true">修改视频库名称或目录</button></div>
          <details v-if="media?.source === 'local' && !media.movie_count && !media.episode_count">
            <summary>修改空媒体库的根路径</summary>
            <p>填写应用服务器可见的路径；Docker 中通常为 /media，而不是 NAS 宿主路径。</p>
            <label>根路径<input v-model="rootPath" placeholder="例如 /media" /></label>
            <button @click="savePath">保存根路径</button>
          </details>
          <button class="primary" @click="check" :disabled="target.kind !== kind || edit">检查并使用此视频库</button>
        </template>
      </template>
      <template v-else-if="mode === 'video'">
        <label>文件存放位置<select v-model.number="newMediaId"><option v-for="m in enabledMedia" :value="m.id" :key="m.id">{{ m.name }}</option></select></label>
        <p>子目录必须独立；若默认视频库使用整个根目录，需先修改空默认库的子目录，再添加其他视频库。</p>
        <VideoLibraryForm v-if="newMediaId" :key="newMediaId + ':' + kind"
          :library="{ media_library_id: newMediaId, kind }" @saved="videoSaved" @cancel="mode = 'existing'" @busy="childBusy = $event" />
        <p v-else>请先添加文件存放位置。</p>
      </template>
      <MediaLibraryCreateForm v-else :key="kind" :kind="kind" compact :smb-driver="driver"
        @created="mediaCreated" @busy="childBusy = $event" />
    </fieldset>
    <p role="status">{{ working ? '正在保存或检查连接，请稍候…' : message }}</p>
    <p v-if="loadError" role="alert">{{ loadError }} <button @click="load" :disabled="working">重新加载</button></p>
  </section>
</template>
<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { listLibs, loadLibs } from '../libraries.js'
import { checkReady } from '../onboarding.js'
import MediaLibraryCreateForm from './MediaLibraryCreateForm.vue'
import VideoLibraryForm from './VideoLibraryForm.vue'
const props = defineProps({
  libraryId: { type: Number, default: null }, initialKind: { type: String, default: 'movie' },
  accept: { type: Function, required: true },
})
const emit = defineEmits(['busy', 'changed'])
const kind = ref(props.initialKind)
const selected = ref(props.libraryId)
const mode = ref('existing')
const libs = ref(listLibs())
const medias = ref([])
const newMediaId = ref(null)
const driver = ref('auto')
const busy = ref(false)
const childBusy = ref(false)
const working = computed(() => busy.value || childBusy.value)
const message = ref('')
const loadError = ref('')
const edit = ref(false)
const rootPath = ref('')
const candidates = computed(() => libs.value.filter(l => l.enabled && l.media_enabled &&
  (l.kind === kind.value || !(l.movie_count + l.episode_count))))
const target = computed(() => candidates.value.find(l => Number(l.id) === selected.value))
const media = computed(() => medias.value.find(m => m.id === target.value?.media_library_id))
const enabledMedia = computed(() => medias.value.filter(m => m.enabled))
watch(working, value => emit('busy', value), { flush: 'sync' })
watch(candidates, values => {
  if (!values.some(l => l.id === selected.value)) selected.value = values.find(l => l.kind === kind.value)?.id || values[0]?.id || null
}, { immediate: true })
watch(media, value => { rootPath.value = value?.path || '' })
watch(kind, () => { edit.value = false; message.value = '' })
async function load() {
  loadError.value = ''
  try {
    const data = await api('/api/media-libraries')
    await loadLibs(api, { force: true })
    libs.value = listLibs(); medias.value = data.items; driver.value = data.smb_driver
    if (!enabledMedia.value.some(m => m.id === newMediaId.value)) newMediaId.value = enabledMedia.value[0]?.id || null
    emit('changed')
    return true
  } catch (e) { loadError.value = '视频库加载失败：' + e.message; return false }
}
async function videoSaved(lib) {
  busy.value = true
  try {
    await load(); selected.value = lib.id; kind.value = lib.kind
    mode.value = 'existing'; edit.value = false; message.value = '已保存，请检查连接并确认目标目录。'
  } finally { busy.value = false }
}
async function mediaCreated(value) {
  busy.value = true
  try {
    await load()
    const lib = value.video_libraries.find(l => l.kind === kind.value) || value.video_libraries[0]
    selected.value = lib.id; kind.value = lib.kind; mode.value = 'existing'
    message.value = '已创建，请检查连接并确认目标目录。'
  } finally { busy.value = false }
}
async function savePath() {
  if (working.value || !media.value) return
  busy.value = true; message.value = ''
  try {
    await api('/api/media-libraries/' + media.value.id, { method: 'PATCH', body: JSON.stringify({ path: rootPath.value.trim() }) })
    await load(); message.value = '根路径已保存，请重新检查。'
  } catch (e) { message.value = '保存失败：' + e.message }
  finally { busy.value = false }
}
async function check() {
  if (working.value || !target.value || target.value.kind !== kind.value) return
  busy.value = true; message.value = ''
  try {
    const lib = target.value
    if (lib.source === 'nfs' || (lib.source === 'smb' && driver.value === 'mount')) {
      const mounted = await api('/api/media-libraries/' + lib.media_library_id + '/mount', { method: 'POST' })
      if (!mounted.ok) throw new Error(mounted.error || '无法挂载，请在设置页检查连接')
    }
    const result = await api('/api/libraries/' + lib.id + '/check', { method: 'POST' })
    if (!checkReady(result)) throw new Error(result.video?.error || result.error || result.reason || '目录不可读取')
    message.value = result.writable && !lib.read_only ? '连接正常，可扫描或上传。' : '目录可读取，可先扫描；上传需要写入权限。'
    await props.accept(lib, result)
  } catch (e) { message.value = '检查失败：' + e.message }
  finally { busy.value = false }
}
onMounted(load)
</script>
<style scoped>
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
label { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; }
input, select { max-width: 100%; min-width: 0; box-sizing: border-box; }
.choices { display: flex; flex-wrap: wrap; gap: 12px; }
.choices label { flex-direction: row; align-items: center; }
dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px 16px; padding: 16px; background: var(--jz-surface); border: 1px solid var(--jz-border); border-radius: 10px; }
dd { margin: 0; overflow-wrap: anywhere; }
.hint { color: var(--jz-text-dim); }
details { margin: 16px 0; }
summary { cursor: pointer; }
.primary { margin-top: 12px; }
</style>
