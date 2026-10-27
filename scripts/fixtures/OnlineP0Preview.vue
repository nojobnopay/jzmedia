<template>
  <main class="online-preview">
    <header class="preview-header">
      <div>
        <p class="eyebrow">jzmedia · P0</p>
        <h1>在线点播</h1>
        <p class="intro">从搜索到播放，验证视频源的接入体验。</p>
      </div>
      <span class="source-label">隔离样片</span>
    </header>

    <form class="search-form" @submit.prevent="search">
      <JzField id="online-query" label="搜索影片或剧集" v-slot="field">
        <input :id="field.id" v-model="query" type="search" placeholder="试试“在线”" autocomplete="off">
      </JzField>
      <JzButton type="submit" variant="primary" icon="search" :loading="loading">搜索</JzButton>
    </form>

    <EmptyState v-if="loading" state="loading" title="正在搜索" text="正在读取验证来源…" />
    <EmptyState v-else-if="searchError" state="error" title="搜索失败" :text="searchError" retry @retry="search" />
    <EmptyState v-else-if="searched && !items.length" state="no-results" title="没有找到相关内容" text="换一个关键词，或搜索“在线”查看隔离样片。" />
    <section v-else-if="items.length" aria-label="搜索结果" class="results">
      <div class="section-heading"><h2>搜索结果</h2><span>{{ items.length }} 部</span></div>
      <article v-for="item in items" :key="item.id" class="result-card">
        <div class="cover" aria-hidden="true"><span>ONLINE</span><strong>两集样片</strong></div>
        <div class="result-copy">
          <p class="source-text">{{ item.source }} · {{ item.episode_count }} 集</p>
          <h3>{{ item.title }}</h3>
          <p>合成视频，用于检验搜索、选集、跳转和续播。</p>
          <JzButton :loading="detailLoading" @click="loadDetail(item)">查看剧集</JzButton>
        </div>
      </article>
    </section>

    <EmptyState v-if="detailError" state="error" title="详情加载失败" :text="detailError" />
    <section v-if="detail" class="detail" aria-label="剧集详情">
      <div class="section-heading"><h2>{{ detail.title }}</h2><span>选择一集开始播放</span></div>
      <JzField id="online-transport" label="播放格式" v-slot="field">
        <select :id="field.id" v-model="transport">
          <option value="mp4">MP4</option>
          <option value="hls">HLS</option>
        </select>
      </JzField>
      <div class="episodes">
        <JzButton v-for="episode in detail.episodes" :key="episode.id" :data-episode-id="episode.id"
          class="episode" @click="playEpisode(episode)">
          <span>{{ episode.title }}</span>
          <span class="episode-progress">{{ progress[keyFor(episode)] > 1 ? `继续 ${formatTime(progress[keyFor(episode)])}` : '开始播放' }}</span>
        </JzButton>
      </div>
    </section>

    <p class="scope-note">此页使用临时生成的视频与独立会话，不连接 NAS 片库。播放进度仅保存在当前标签页。</p>

    <JzDialog v-if="active" :title="active.episode.title" size="large" close-label="关闭播放器" @close="closePlayer">
      <div class="playback" :data-transport="descriptor?.transport || ''" :data-engine="engineName">
        <video :key="epoch" ref="video" class="video" playsinline muted preload="metadata"
          @loadedmetadata="onMetadata" @timeupdate="onTime" @play="paused = false" @pause="paused = true"
          @ended="onEnded" @error="onMediaError" />
        <p v-if="preparing" role="status" class="playback-status">正在准备播放…</p>
        <EmptyState v-if="playError" state="error" title="播放失败" :text="playError">
          <JzButton @click="playEpisode(active.episode)">重试播放</JzButton>
        </EmptyState>
        <template v-if="descriptor && !playError">
          <div class="time-row"><span>{{ formatTime(position) }} / {{ formatTime(duration) }}</span><span>{{ descriptor.transport.toUpperCase() }} · {{ engineName }}</span></div>
          <label class="seek-label">播放进度
            <input type="range" aria-label="播放进度" :min="0" :max="duration || 1" :value="position" step="1"
              :disabled="preparing" @change="seekTo(Number($event.target.value))">
          </label>
          <div class="player-controls">
            <JzButton :disabled="preparing" :icon="paused ? 'play' : 'pause'" @click="togglePlayback">{{ paused ? '播放' : '暂停' }}</JzButton>
            <JzButton :disabled="preparing" @click="seekTo(position - 10)">后退 10 秒</JzButton>
            <JzButton :disabled="preparing" @click="seekTo(position + 10)">前进 10 秒</JzButton>
            <JzButton @click="toggleMute">{{ muted ? '开启声音' : '静音' }}</JzButton>
          </div>
          <div class="next-episodes" aria-label="播放时选集">
            <JzButton v-for="episode in detail.episodes" :key="episode.id" :aria-pressed="episode.id === active.episode.id"
              :disabled="episode.id === active.episode.id" @click="playEpisode(episode)">第 {{ episode.number }} 集</JzButton>
          </div>
        </template>
      </div>
    </JzDialog>
  </main>
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import Hls from 'hls.js'
import JzButton from '../../frontend/src/components/JzButton.vue'
import JzField from '../../frontend/src/components/JzField.vue'
import JzDialog from '../../frontend/src/components/JzDialog.vue'
import EmptyState from '../../frontend/src/components/EmptyState.vue'
import { createPlaybackPageLifecycle } from '../../frontend/src/playbackLifecycle.js'

const query = ref('在线'), loading = ref(false), searched = ref(false), searchError = ref(''), items = ref([])
const detail = ref(null), detailLoading = ref(false), detailError = ref(''), transport = ref('mp4')
const active = ref(null), descriptor = ref(null), video = ref(null), epoch = ref(0)
const position = ref(0), duration = ref(0), paused = ref(true), muted = ref(true)
const preparing = ref(false), playError = ref(''), engineName = ref(''), progress = ref({})
let hls = null, searchController, detailController, playController, pendingResume = 0
const progressStore = 'jzmedia-online-p0-progress'
try { progress.value = JSON.parse(sessionStorage.getItem(progressStore) || '{}') } catch { /* No persistent data required. */ }

function formatTime(value) {
  const total = Math.max(0, Math.floor(Number(value) || 0))
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}
function keyFor(episode) { return `${detail.value?.source}:${detail.value?.id}:${episode.id}` }
function capture() {
  return active.value ? { key: active.value.key, position: video.value?.ended ? 0 : video.value?.currentTime || position.value,
    episode: active.value.episode } : null
}
function save(snapshot = capture()) {
  if (!snapshot || !Number.isFinite(snapshot.position)) return
  progress.value[snapshot.key] = Math.max(0, snapshot.position)
  try { sessionStorage.setItem(progressStore, JSON.stringify(progress.value)) } catch { /* Playback still works without storage. */ }
}
async function request(url, signal) {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    const message = await response.json().catch(() => ({}))
    throw new Error(typeof message.detail === 'string' ? message.detail : `请求失败（${response.status}）`)
  }
  return response.json()
}
async function search() {
  searchController?.abort()
  const controller = searchController = new AbortController()
  loading.value = true; searchError.value = ''; searched.value = false
  try {
    const result = await request(`/api/p0/search?q=${encodeURIComponent(query.value)}`, controller.signal)
    if (searchController !== controller) return
    items.value = result.items; searched.value = true
  } catch (error) {
    if (error.name !== 'AbortError' && searchController === controller) searchError.value = error.message
  } finally { if (searchController === controller) loading.value = false }
}
async function loadDetail(item) {
  detailController?.abort()
  const controller = detailController = new AbortController()
  detailLoading.value = true; detailError.value = ''
  try {
    const result = await request(`/api/p0/items/${encodeURIComponent(item.id)}`, controller.signal)
    if (detailController === controller) detail.value = result
  } catch (error) {
    if (error.name !== 'AbortError' && detailController === controller) detailError.value = error.message
  } finally { if (detailController === controller) detailLoading.value = false }
}
function release(sessionId) {
  if (!sessionId) return Promise.resolve()
  return fetch(`/api/p0/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE', keepalive: true }).catch(() => {})
}
function disposeMedia() {
  epoch.value++
  playController?.abort(); playController = null
  const old = descriptor.value
  descriptor.value = null
  hls?.destroy(); hls = null
  if (video.value) {
    video.value.pause()
    video.value.removeAttribute('src')
    video.value.load()
  }
  paused.value = true; preparing.value = false; engineName.value = ''
  return release(old?.session_id)
}
async function playEpisode(episode) {
  save()
  void disposeMedia()
  const current = epoch.value
  const controller = playController = new AbortController()
  active.value = { episode, key: keyFor(episode) }
  playError.value = ''; preparing.value = true; position.value = 0; duration.value = 0; muted.value = true
  pendingResume = Number(progress.value[active.value.key]) || 0
  await nextTick()
  try {
    const data = await request(`/api/p0/play/${encodeURIComponent(detail.value.id)}/${encodeURIComponent(episode.id)}?transport=${transport.value}`, controller.signal)
    if (current !== epoch.value) { void release(data.session_id); return }
    const mediaUrl = new URL(data.media_url, location.origin)
    if (mediaUrl.origin !== location.origin || !mediaUrl.pathname.startsWith('/api/p0/media/')) {
      void release(data.session_id)
      throw new Error('验证播放器只接受当前服务的媒体地址')
    }
    descriptor.value = data; duration.value = data.duration; video.value.muted = true
    if (data.transport === 'hls' && Hls.isSupported()) {
      engineName.value = 'hls.js'
      hls = new Hls({ maxBufferLength: 8, maxMaxBufferLength: 12, capLevelToPlayerSize: true })
      hls.on(Hls.Events.ERROR, (_event, error) => {
        if (current === epoch.value && error.fatal) {
          playError.value = `HLS 加载失败：${error.details}`; preparing.value = false
        }
      })
      hls.loadSource(mediaUrl.href)
      hls.attachMedia(video.value)
    } else if (data.transport === 'mp4' || video.value.canPlayType('application/vnd.apple.mpegurl')) {
      engineName.value = data.transport === 'mp4' ? '原生 MP4' : '原生 HLS'
      video.value.src = mediaUrl.href
    } else throw new Error('当前浏览器不支持 HLS 播放')
  } catch (error) {
    if (error.name !== 'AbortError' && current === epoch.value) {
      playError.value = error.message; preparing.value = false
    }
  }
}
function onMetadata(event) {
  if (event.target !== video.value || !descriptor.value) return
  duration.value = Number.isFinite(video.value.duration) ? video.value.duration : descriptor.value.duration
  if (pendingResume > 0 && pendingResume < duration.value - 0.5) video.value.currentTime = pendingResume
  pendingResume = 0; preparing.value = false
  void video.value.play().catch(() => { paused.value = true })
}
function onTime(event) {
  if (event.target !== video.value || !descriptor.value) return
  position.value = video.value.currentTime
}
function onEnded() { save({ ...capture(), position: 0 }) }
function onMediaError(event) {
  if (event.target !== video.value || !descriptor.value || !video.value.error) return
  playError.value = `视频解码失败（${video.value.error.code}）`; preparing.value = false
}
function seekTo(target) {
  if (!video.value || preparing.value) return
  video.value.currentTime = Math.min(Math.max(0, target), Math.max(0, duration.value - 0.5))
  position.value = video.value.currentTime
}
function togglePlayback() {
  if (!video.value) return
  if (video.value.paused) void video.value.play().catch(error => { playError.value = error.message })
  else video.value.pause()
}
function toggleMute() {
  muted.value = !muted.value
  if (video.value) video.value.muted = muted.value
}
function closePlayer() { save(); void disposeMedia(); active.value = null }
const lifecycle = createPlaybackPageLifecycle({ capture, save, stop: disposeMedia,
  restore: snapshot => { if (snapshot?.episode && active.value) return playEpisode(snapshot.episode) } })
onMounted(() => { lifecycle.bind(window) })
onUnmounted(() => { searchController?.abort(); detailController?.abort(); void lifecycle.leave(); lifecycle.unbind() })
</script>

<style scoped>
.online-preview { max-width: 1080px; margin: 0 auto; padding: 52px 32px; color: var(--jz-text); }
.preview-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.eyebrow { color: var(--jz-text-dim); font-size: var(--jz-font-s); letter-spacing: .12em; margin: 0 0 8px; }
h1 { margin: 0; font-size: 36px; line-height: 1.4; }
.intro, .scope-note, .result-copy > p { color: var(--jz-text-dim); line-height: 1.7; }
.source-label { padding: 8px 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); color: var(--jz-text-dim); white-space: nowrap; }
.search-form { display: flex; align-items: flex-end; gap: 12px; margin: 20px 0 32px; }
.search-form .jz-field { flex: 1; margin: 0; }
.search-form > .jz-button { min-width: 100px; }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin: 24px 0 16px; }
.section-heading h2 { margin: 0; font-size: var(--jz-font-xl); }
.section-heading > span { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.result-card { display: grid; grid-template-columns: 160px 1fr; gap: 24px; padding: 24px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); background: var(--jz-surface); }
.cover { display: flex; flex-direction: column; justify-content: flex-end; min-height: 170px; gap: 8px; padding: 20px; box-sizing: border-box; background: linear-gradient(145deg, var(--jz-surface-3), var(--jz-surface-2)); border-left: 4px solid var(--jz-accent); border-radius: var(--jz-radius-s); }
.cover span { color: var(--jz-text-dim); font-size: 12px; letter-spacing: .14em; }
.cover strong { font-size: 22px; }
.result-copy { min-width: 0; }
.result-copy h3 { margin: 8px 0; font-size: 24px; }
.result-copy .source-text { margin: 0; font-size: var(--jz-font-s); }
.detail { margin-top: 32px; }
.detail .jz-field { max-width: 260px; }
.episodes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.episode { justify-content: space-between; flex-wrap: wrap; gap: 8px; min-height: 64px; }
.episode-progress { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.scope-note { margin-top: 32px; font-size: var(--jz-font-s); }
.video { display: block; width: 100%; aspect-ratio: 16 / 9; background: #000; border-radius: var(--jz-radius-s); }
.playback-status { color: var(--jz-text-dim); }
.time-row { display: flex; justify-content: space-between; gap: 12px; margin-top: 16px; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.seek-label { display: block; margin: 10px 0 12px; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.seek-label input { display: block; width: 100%; min-height: 44px; padding: 0; accent-color: var(--jz-accent); }
.player-controls, .next-episodes { display: flex; flex-wrap: wrap; gap: 8px; }
.next-episodes { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--jz-border); }
@media (max-width: 700px) {
  .online-preview { padding: 24px 16px; }
  h1 { font-size: 28px; }
  .preview-header { gap: 12px; }
  .source-label { font-size: 12px; }
  .search-form { gap: 8px; }
  .search-form > .jz-button { min-width: 72px; }
  .result-card { grid-template-columns: 92px 1fr; gap: 16px; padding: 16px; }
  .cover { min-height: 150px; padding: 10px; }
  .cover span { font-size: 10px; letter-spacing: .04em; }
  .cover strong { font-size: 17px; }
  .result-copy h3 { font-size: 20px; }
  .result-copy > p { font-size: var(--jz-font-s); }
  .episodes { grid-template-columns: 1fr; }
  .section-heading { align-items: flex-start; flex-direction: column; gap: 6px; }
  .time-row { flex-wrap: wrap; gap: 4px 12px; }
  .player-controls { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .player-controls .jz-button { padding-inline: 10px; }
}
</style>
