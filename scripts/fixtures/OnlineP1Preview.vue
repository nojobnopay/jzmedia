<template>
  <main class="online-p1-preview">
    <header>
      <p class="eyebrow">jzmedia · 隔离验证</p>
      <h1>在线播放器</h1>
      <p>使用正式会话与透传网关，检验 MP4、HLS、跳转和退出回收。</p>
    </header>
    <EmptyState v-if="loading" state="loading" title="正在准备隔离来源" text="正在建立当前设备会话…" />
    <EmptyState v-else-if="error" state="error" title="验证来源未就绪" :text="error" retry @retry="load" />
    <section v-else aria-label="隔离样片" class="fixtures">
      <article v-for="item in items" :key="item.key" class="fixture-card">
        <div>
          <span class="transport">{{ item.transport.toUpperCase() }} · 24 秒合成样片</span>
          <h2>{{ item.title }}</h2>
          <p>{{ progress[item.key] > 1 ? `从 ${Math.floor(progress[item.key])} 秒继续` : '验证开始、暂停、跳转与关闭。' }}</p>
        </div>
        <JzButton :data-online-item="item.key" icon="play" variant="primary" @click="select(item.key)">
          {{ progress[item.key] > 1 ? '继续播放' : '开始播放' }}
        </JzButton>
      </article>
    </section>
    <p class="scope-note">只使用临时数据库和开发机生成的视频。此入口不出现在产品导航中，进度仅保存在当前标签页。</p>
    <OnlinePlayerModal v-if="active" :online-identity="active.identity" :title="active.title"
      :initial-position="initialPosition" @progress="onProgress" @ended="onEnded" @close="close" />
  </main>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api, setToken } from '../../frontend/src/api.js'
import JzButton from '../../frontend/src/components/JzButton.vue'
import EmptyState from '../../frontend/src/components/EmptyState.vue'
import OnlinePlayerModal from '../../frontend/src/components/OnlinePlayerModal.vue'

const storageKey = 'jzmedia-online-p1-preview-progress'
const loading = ref(true), error = ref(''), items = ref([]), active = ref(null)
const initialPosition = ref(0), progress = ref({})
let disposed = false
let controller = null
try { progress.value = JSON.parse(sessionStorage.getItem(storageKey) || '{}') } catch { /* Isolated storage is optional. */ }

function keyFor(identity) {
  return items.value.find(item => item.identity.source_id === identity?.source_id
    && item.identity.item_id === identity?.item_id && item.identity.kind === identity?.kind
    && item.identity.episode_id === (identity?.episode_id ?? null))?.key || ''
}

function save(key, position) {
  if (!key || !Number.isFinite(position)) return
  progress.value = { ...progress.value, [key]: Math.max(0, position) }
  try { sessionStorage.setItem(storageKey, JSON.stringify(progress.value)) } catch { /* Playback remains available. */ }
}

function onProgress(payload) {
  const position = Number(payload?.position), duration = Number(payload?.duration)
  save(keyFor(payload?.identity), duration > 0 && position >= duration - .5 ? 0 : position)
}

function onEnded(identity) {
  save(keyFor(identity), 0)
}

function select(key, position) {
  const item = items.value.find(candidate => candidate.key === key)
  if (!item) return false
  initialPosition.value = Number.isFinite(position) ? Math.max(0, position) : Number(progress.value[key]) || 0
  // Keep the component instance when selecting another identity, so its real
  // generation guards and late-session cleanup are exercised by the fixture.
  active.value = item
  return true
}

function close() {
  active.value = null
}

async function load() {
  controller?.abort()
  const current = controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const catalog = await api('/api/p1/catalog', { signal: current.signal })
    if (disposed || current !== controller) return
    setToken(catalog.api_token)
    await api('/api/viewing/session', { signal: current.signal })
    if (disposed || current !== controller) return
    items.value = catalog.items
  } catch (failure) {
    if (!disposed && current === controller && !current.signal.aborted) error.value = failure.message
  } finally {
    if (!disposed && current === controller) loading.value = false
  }
}

onMounted(() => {
  window.__jzOnlineFixture = {
    select, close,
    snapshot: () => ({ ready: !loading.value && !error.value, error: error.value,
      activeKey: active.value?.key || '', initialPosition: initialPosition.value, progress: { ...progress.value },
      items: items.value.map(item => ({ ...item, identity: { ...item.identity } })) }),
  }
  void load()
})

onUnmounted(() => {
  disposed = true
  controller?.abort()
  delete window.__jzOnlineFixture
})
</script>

<style scoped>
.online-p1-preview { max-width: 1000px; margin: 0 auto; padding: 40px 24px; color: var(--jz-text); }
header { margin-bottom: 28px; }
.eyebrow { color: var(--jz-text-faint); font-size: var(--jz-font-s); letter-spacing: .12em; }
h1 { margin: 8px 0 12px; font-size: 30px; }
header > p:last-child, .fixture-card p, .scope-note { color: var(--jz-text-dim); line-height: 1.7; }
.fixtures { display: grid; gap: 16px; }
.fixture-card { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 22px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); background: var(--jz-surface); }
.transport { color: var(--jz-text-faint); font-size: var(--jz-font-s); }
h2 { margin: 8px 0; font-size: 18px; }
.fixture-card p { margin: 0; }
.scope-note { margin-top: 24px; font-size: 13px; }
@media (max-width: 700px) {
  .online-p1-preview { padding: 24px 16px; }
  .fixture-card { align-items: stretch; flex-direction: column; gap: 16px; padding: 18px; }
}
</style>
