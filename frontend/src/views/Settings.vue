<template>
  <div class="page">
    <h2>设置</h2>

    <section class="card-block">
      <h3>库状态</h3>
      <p v-if="s" class="meta-line">媒体目录：{{ s.media_root }} · 语言：{{ s.tmdb_language }} · TMDB Token：{{ s.tmdb_configured ? '已配' : '未配' }} · 图片源：{{ s.tmdb_image_base }}</p>
      <div v-if="stats" class="stat-grid">
        <div class="stat"><b>{{ stats.grouped }}</b><span>影片</span></div>
        <div class="stat"><b>{{ stats.versions }}</b><span>文件版本</span></div>
        <div class="stat"><b>{{ stats.needs_review }}</b><span>待确认</span></div>
        <div class="stat"><b>{{ stats.no_match }}</b><span>未匹配</span></div>
        <div class="stat warn"><b>{{ stats.missing_files }}</b><span>失效文件</span></div>
        <div class="stat"><b>{{ stats.tmdb_cache }}</b><span>镜像缓存</span></div>
        <div class="stat"><b>{{ stats.persons }}</b><span>人物</span></div>
        <div class="stat"><b>{{ fmtBytes(stats.db_bytes) }}</b><span>数据库</span></div>
        <div class="stat"><b>{{ fmtBytes(stats.posters_bytes) }}</b><span>海报</span></div>
      </div>
      <div class="bar">
        <button @click="testTmdb" :disabled="!!busy">测试TMDB连接</button>
        <span>{{ tmdbMsg }}</span>
      </div>
    </section>

    <section class="card-block">
      <h3>媒体库同步</h3>
      <p class="hint">在软件之外增删视频后用这里同步：先扫描新增入库，再检查并清理失效条目。</p>
      <div class="bar">
        <button @click="doScan" :disabled="!!busy">{{ busy === 'scan' ? '扫描中…' : '扫描新文件' }}</button>
        <span>{{ scanMsg }}</span>
      </div>
      <div class="bar">
        <button @click="loadMissing" :disabled="!!busy">检查失效条目</button>
        <button v-if="missing.length" @click="toggleAllMissing">{{ allChecked ? '全不选' : '全选' }}</button>
        <span v-if="missing.length">共 {{ missing.length }} 条失效</span>
      </div>
      <ul v-if="missing.length" class="miss-list">
        <li v-for="m in missing" :key="m.id" class="miss-row">
          <input type="checkbox" :value="m.id" v-model="checkedMissing" />
          <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
          <span class="miss-path">{{ m.file_path }}</span>
        </li>
      </ul>
      <div v-if="missing.length" class="bar">
        <button @click="doClean" :disabled="!!busy || !checkedMissing.length">
          {{ busy === 'clean' ? '清理中…' : `删除选中 (${checkedMissing.length})` }}
        </button>
        <span>{{ cleanMsg }}</span>
      </div>
    </section>

    <section class="card-block">
      <h3>元数据维护</h3>
      <div class="bar">
        <button @click="doBackfill" :disabled="!!busy">{{ busy === 'backfill' ? '补数据中…' : '补产地信息' }}</button>
        <span>{{ backfillMsg }}</span>
      </div>
      <div class="bar">
        <button @click="doRefreshAll" :disabled="!!busy">
          {{ busy === 'refresh' ? '刷新中…' : (armRefresh ? `确认刷新全部（约${stats ? stats.grouped : '?'}部）` : '刷新全部TMDB数据') }}
        </button>
        <span>{{ refreshMsg }}</span>
      </div>
      <p v-if="armRefresh" class="hint warn-text">将逐部请求 TMDB（以 limit 截断），无变化的不动，手工标题不受影响。再点一次执行。</p>
      <div class="bar">
        <button @click="doRebuildNfo" :disabled="!!busy">{{ busy === 'nfo' ? '重建中…' : '重建全部NFO' }}</button>
        <span>{{ nfoMsg }}</span>
      </div>
      <div class="bar">
        <button @click="doRebuildFts" :disabled="!!busy">{{ busy === 'fts' ? '重建中…' : '重建搜索索引' }}</button>
        <span>{{ ftsMsg }}</span>
      </div>
    </section>

    <section class="card-block">
      <h3>显示</h3>
      <div class="slider-row">
        <label>字体大小 <b>{{ prefs.fontSize }}px</b></label>
        <input type="range" min="13" max="20" step="1" v-model.number="prefs.fontSize" @input="saveDisplay" />
      </div>
      <div class="slider-row">
        <label>海报墙密度 <b>{{ prefs.posterMin }}px</b></label>
        <input type="range" min="120" max="200" step="10" v-model.number="prefs.posterMin" @input="saveDisplay" />
      </div>
      <div class="bar">
        <button @click="resetDisplay">恢复默认</button>
      </div>
    </section>

    <section class="card-block">
      <h3>文件整理</h3>
      <p class="hint">按“电影名 (年份)/电影名 (年份).ext”归档，先预览再执行。</p>
      <div class="bar">
        <button @click="loadPreview" :disabled="!!busy">预览</button>
        <button @click="doRename" :disabled="!!busy || !plans.length">{{ busy === 'rename' ? '执行中…' : '执行整理' }}</button>
        <span>{{ renameMsg }}</span>
      </div>
      <ul v-if="plans.length" class="plan-list">
        <li v-for="p in plans" :key="p.id" class="plan-row">
          <span class="plan-from">{{ p.from }}</span>
          <span class="plan-arrow">→</span>
          <span class="plan-to">{{ p.to }}</span>
          <span v-if="p.status" :class="['plan-status', p.status === 'moved' ? 'ok' : 'fail']">{{ p.status }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api.js'
import { loadPrefs, savePrefs, PREF_DEFAULTS } from '../prefs.js'

const s = ref(null)
const stats = ref(null)
const busy = ref(null) // scan|clean|backfill|refresh|nfo|fts|rename

const tmdbMsg = ref('')
const scanMsg = ref('')
const cleanMsg = ref('')
const backfillMsg = ref('')
const refreshMsg = ref('')
const nfoMsg = ref('')
const ftsMsg = ref('')
const renameMsg = ref('')

const missing = ref([])
const checkedMissing = ref([])
const allChecked = computed(() => missing.value.length > 0 && checkedMissing.value.length === missing.value.length)

const plans = ref([])

const prefs = ref(loadPrefs())

function fmtBytes(n) {
  n = Number(n) || 0
  if (n < 1024) return n + 'B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'M'
  return (n / 1024 / 1024 / 1024).toFixed(2) + 'G'
}

async function loadStats() {
  try { stats.value = await api('/api/jobs/stats') } catch (e) { /* 忽略 */ }
}

async function testTmdb() {
  tmdbMsg.value = '测试中…'
  const t0 = performance.now()
  try {
    await api('/api/tmdb/search?q=' + encodeURIComponent('阿凡达'))
    tmdbMsg.value = `连接正常（${Math.round(performance.now() - t0)}ms）`
  } catch (e) {
    tmdbMsg.value = '连接失败：' + e.message
  }
}

async function doScan() {
  busy.value = 'scan'
  scanMsg.value = ''
  try {
    const d = await api('/api/scan', { method: 'POST' })
    const c = {}
    for (const r of d.results) c[r.status] = (c[r.status] || 0) + 1
    const ok = (c.ok || 0) + (c.ok_needs_review || 0)
    scanMsg.value = `完成：新增/更新 ${ok}，已同步跳过 ${c.skipped_cached || 0}，未匹配 ${c.no_match || 0}`
    await loadStats()
    await loadMissing(true)
  } catch (e) {
    scanMsg.value = '扫描失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function loadMissing(silent) {
  if (!silent) cleanMsg.value = ''
  try {
    const d = await api('/api/files/missing')
    missing.value = d.items
    checkedMissing.value = d.items.map(m => m.id)
    if (!silent) cleanMsg.value = d.total ? '' : '没有失效条目'
  } catch (e) {
    if (!silent) cleanMsg.value = '检查失败：' + e.message
  }
}

function toggleAllMissing() {
  checkedMissing.value = allChecked.value ? [] : missing.value.map(m => m.id)
}

async function doClean() {
  busy.value = 'clean'
  cleanMsg.value = ''
  try {
    const d = await api('/api/files/clean', { method: 'POST', body: JSON.stringify({ ids: checkedMissing.value }) })
    const removed = new Set(d.results.map(r => r.id))
    missing.value = missing.value.filter(m => !removed.has(m.id))
    checkedMissing.value = checkedMissing.value.filter(id => !removed.has(id))
    cleanMsg.value = `已删除 ${d.deleted}/${d.total}` + (d.failed.length ? `，失败 ${d.failed.length}` : '')
    await loadStats()
  } catch (e) {
    cleanMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doBackfill() {
  busy.value = 'backfill'
  backfillMsg.value = ''
  try {
    const d = await api('/api/jobs/backfill-meta', { method: 'POST', body: JSON.stringify({}) })
    backfillMsg.value = `回填完成：${d.ok}/${d.total}，失败 ${d.failed.length}`
  } catch (e) {
    backfillMsg.value = '回填失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armRefresh = ref(false)
async function doRefreshAll() {
  if (!armRefresh.value) {
    armRefresh.value = true
    refreshMsg.value = '再点一次确认执行'
    return
  }
  armRefresh.value = false
  busy.value = 'refresh'
  refreshMsg.value = ''
  try {
    const d = await api('/api/jobs/tmdb-refresh', { method: 'POST', body: JSON.stringify({ limit: 5000 }) })
    const changed = d.results.filter(r => r.changed).length
    refreshMsg.value = `完成：${d.total} 部中有变化 ${changed} 部，失败 ${d.failed.length}`
  } catch (e) {
    refreshMsg.value = '刷新失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doRebuildNfo() {
  busy.value = 'nfo'
  nfoMsg.value = ''
  try {
    const d = await api('/api/jobs/rebuild-nfo', { method: 'POST', body: JSON.stringify({}) })
    nfoMsg.value = `完成：重写 ${d.ok}/${d.total}，跳过缺失 ${d.skipped_missing}，失败 ${d.failed.length}`
  } catch (e) {
    nfoMsg.value = '重建失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doRebuildFts() {
  busy.value = 'fts'
  ftsMsg.value = ''
  try {
    const d = await api('/api/jobs/rebuild-fts', { method: 'POST' })
    ftsMsg.value = `索引已重建（${d.rows} 行）`
  } catch (e) {
    ftsMsg.value = '重建失败：' + e.message
  } finally {
    busy.value = null
  }
}

function saveDisplay() {
  savePrefs({ ...prefs.value })
}

function resetDisplay() {
  prefs.value = { ...PREF_DEFAULTS }
  savePrefs({ ...prefs.value })
}

async function loadPreview() {
  renameMsg.value = ''
  try {
    const d = await api('/api/files/preview')
    plans.value = d.plans
    if (!d.plans.length) renameMsg.value = '没有需要整理的'
  } catch (e) {
    renameMsg.value = '预览失败：' + e.message
  }
}

async function doRename() {
  busy.value = 'rename'
  renameMsg.value = ''
  try {
    const d = await api('/api/files/rename', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
    plans.value = d.results
    const ok = d.results.filter(r => r.status === 'moved').length
    renameMsg.value = `执行完毕：移动 ${ok}/${d.results.length}`
  } catch (e) {
    renameMsg.value = '执行失败：' + e.message
  } finally {
    busy.value = null
  }
}

onMounted(async () => {
  s.value = await api('/api/settings')
  await loadStats()
  await loadPreview()
  await loadMissing(true)
})
</script>
<style scoped>
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.meta-line { color: #aaa; font-size: 0.875rem; margin: 8px 0; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; margin: 8px 0; }
.stat { background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 8px 4px; text-align: center; }
.stat b { display: block; font-size: 1.25rem; }
.stat span { color: #888; font-size: 0.75rem; }
.stat.warn b { color: #ff8a8a; }
.slider-row { display: flex; align-items: center; gap: 12px; padding: 6px 12px; }
.slider-row label { min-width: 150px; font-size: 0.875rem; }
.slider-row input[type="range"] { flex: 1; }
.miss-list, .plan-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.miss-row, .plan-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.miss-title { white-space: nowrap; }
.miss-path, .plan-from { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-arrow { color: #6ab0ff; }
.plan-to { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-status { font-size: 0.75rem; }
.plan-status.ok { color: #7ed321; }
.plan-status.fail { color: #ff8a8a; }
</style>
