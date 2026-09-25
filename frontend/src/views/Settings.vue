<template>
  <div class="settings-layout">
    <aside class="side-nav">
      <template v-for="(n, i) in navs" :key="n.id">
        <div v-if="n.group && (i === 0 || navs[i - 1].group !== n.group)" class="nav-group">{{ n.group }}</div>
        <button :class="{ on: active === n.id }" @click="go(n)">{{ n.label }}</button>
      </template>
    </aside>
    <div class="page settings-main">
      <h2>设置</h2>

      <section id="sec-status" class="card-block">
        <h3>库状态 <span class="fhint">全部媒体库合计</span></h3>
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

      <LibrariesPanel ref="librariesRef" @changed="onLibrariesChanged" />

      <section id="sec-tmdb" class="card-block">
        <h3>TMDB 配置</h3>
        <p class="hint">库里的值优先于 `.env`，保存后免重启生效；密钥输入框留空表示不动它。密钥只显示脱敏后 4 位，不回显明文。</p>
        <div class="tmdb-grid">
          <label>Read Token <span class="src-badge">{{ srcText(s?.tmdb_read_token_source) }} {{ s?.tmdb_read_token_masked || '未配' }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.readToken" type="password" placeholder="粘贴新的 Bearer Token，留空不动" style="flex:1" autocomplete="off" />
            <button v-if="tmdbForm.readToken" @click="tmdbForm.readToken = ''" :disabled="!!busy">清空输入</button>
          </div>
          <label>API Key <span class="src-badge">{{ srcText(s?.tmdb_api_key_source) }} {{ s?.tmdb_api_key_masked || '未配' }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.apiKey" type="password" placeholder="Token 优先；无 Token 才用 Key，留空不动" style="flex:1" autocomplete="off" />
            <button v-if="tmdbForm.apiKey" @click="tmdbForm.apiKey = ''" :disabled="!!busy">清空输入</button>
          </div>
          <label>代理 <span class="src-badge">{{ srcText(s?.tmdb_proxy_source) }}{{ s?.tmdb_proxy ? '' : ' · 直连' }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.proxy" placeholder="http://host:port，留空=直连" style="flex:1" autocomplete="off" />
          </div>
          <label>语言 <span class="src-badge">{{ srcText(s?.tmdb_language_source) }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.language" placeholder="zh-CN" style="flex:1" autocomplete="off" />
          </div>
          <label>图片源 <span class="src-badge">{{ srcText(s?.tmdb_image_base_source) }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.imageBase" placeholder="https://image.tmdb.org" style="flex:1" autocomplete="off" />
          </div>
        </div>
        <div class="bar">
          <button @click="saveTmdb" :disabled="!!busy">{{ busy === 'tmdb' ? '保存中…' : '保存并测试' }}</button>
          <button @click="clearTmdb" :disabled="!!busy">{{ armClearTmdb ? '确认恢复跟随 .env' : '恢复跟随 .env' }}</button>
          <span>{{ tmdbCfgMsg }}</span>
        </div>
        <p v-if="armClearTmdb" class="hint warn-text">将清空库里的 5 项 TMDB 配置，改回跟随 .env/默认值。再点一次执行。</p>

        <div class="provider-block">
          <h4>元数据降级链
            <span class="fhint">连续失败 {{ providerInfo?.fail_threshold ?? 3 }} 次自动冷却
              {{ Math.round((providerInfo?.cooldown_sec ?? 600) / 60) }} 分钟；冷却期内的来源会被搜索链跳过</span>
          </h4>
          <div class="provider-row" v-for="p in providers" :key="p.name">
            <span class="p-name">{{ p.label }}</span>
            <span class="p-state" :class="{ cool: !p.available }">{{ providerStateText(p) }}</span>
            <button v-if="!p.available" @click="resetProvider(p.name)" :disabled="!!busy">重置冷却</button>
            <span v-if="p.last_error" class="fhint p-err" :title="p.last_error">{{ p.last_error }}</span>
          </div>
          <div class="provider-row chain-editor">
            <span class="fhint">库级降级链（顺序固定，未勾选=不启用；不配置=默认 本地→TMDB→Wikidata；TVmaze/Bangumi 为无 key 外源）</span>
          </div>
          <div class="provider-row">
            <select v-model.number="chainLibId" @change="loadChainFor" :disabled="!libList.length">
              <option v-for="l in libList" :key="l.id" :value="l.id">{{ l.name }}</option>
            </select>
            <label v-for="name in CHAIN_PROVIDERS" :key="name" class="chain-item">
              <input type="checkbox" :value="name" v-model="chainSel" />
              {{ CHAIN_LABELS[name] }}
            </label>
            <button @click="saveChain" :disabled="!!busy || chainLibId == null">保存链路</button>
            <span>{{ chainMsg }}</span>
          </div>
        </div>
      </section>

      <section id="sec-auth" class="card-block">
        <h3>访问控制</h3>
        <p class="hint">配置后，写操作（扫描/编辑/整理/删除/设置等 POST/PUT/PATCH/DELETE）需要访问令牌；GET 读取、海报与电视/Kodi 直链不受影响。令牌存库（优先于 <code>.env</code> 的 <code>JZMEDIA_TOKEN</code>），免重启生效；留空并保存 = 关闭鉴权（恢复完全开放）。</p>
        <div class="tmdb-grid">
          <label>访问令牌 <span class="src-badge">{{ srcText(s?.jzmedia_token_source) }} {{ s?.jzmedia_token_masked || '未设置（开放）' }}</span></label>
          <div class="bar">
            <input v-model="authForm.token" type="password" placeholder="新令牌（≥8 位），留空保存=关闭鉴权" style="flex:1" autocomplete="off" />
            <button @click="saveAuth" :disabled="!!busy">{{ busy === 'auth' ? '保存中…' : '保存' }}</button>
            <span>{{ authMsg }}</span>
          </div>
        </div>
        <p class="hint">浏览器首次遇到 401 会弹输入框；输入后令牌存在本机 localStorage。令牌遗失时可直接清空数据库该项或改 `.env` 后重启。</p>
      </section>

      <section id="sec-display" class="card-block">
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

      <section id="sec-index" class="card-block">
        <h3>搜索索引 / 离线数据</h3>
        <p class="hint">全文搜索异常时的修复口（全局，不区分库）。</p>
        <div class="bar">
          <button @click="doRebuildFts" :disabled="!!busy">{{ busy === 'fts' ? '重建中…' : '重建搜索索引' }}</button>
          <span>{{ ftsMsg }}</span>
        </div>
        <div class="bar">
          <input v-model="imdbPath" placeholder="IMDb 数据集路径 title.basics.tsv(.gz)（留空读 IMDB_DATASET_PATH）" style="flex:1" />
          <button @click="doImportImdb" :disabled="!!busy">{{ busy === 'imdb' ? '导入中…' : '导入 IMDb 离线数据' }}</button>
          <button v-if="busy === 'imdb'" @click="cancelImportImdb">取消</button>
          <span>{{ imdbMsg }}</span>
        </div>
        <p class="hint">IMDb 数据集作为离线候选（标题/年份/IMDb ID），配合本地缓存/外部源提升无网匹配；文件需事先放在服务器可读路径。</p>
      </section>

      <LibraryToolsPanel ref="toolsRef" :libs="libList" :current-media-id="currentId"
        @changed="onToolsChanged" />
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import LibrariesPanel from '../components/LibrariesPanel.vue'
import LibraryToolsPanel from '../components/LibraryToolsPanel.vue'
import { fmtBytes } from '../format.js'
import { api, setToken } from '../api.js'
import { currentMediaId, listLibs, loadLibs } from '../libraries.js'
import { loadPrefs, savePrefs, PREF_DEFAULTS } from '../prefs.js'

const route = useRoute()

// 库工具锚点（深链：/settings?sec=sec-restore&ids=… / ?sec=sec-pipeline）
const LIB_SECS = new Set(['sec-pipeline', 'sec-sync', 'sec-pending', 'sec-organize',
  'sec-meta', 'sec-tvorganize', 'sec-restore', 'sec-files', 'sec-libtools'])

const s = ref(null)
const stats = ref(null)
const busy = ref(null) // tmdb|auth|fts

const tmdbMsg = ref('')
const tmdbCfgMsg = ref('')
const armClearTmdb = ref(false)
const tmdbForm = ref({ readToken: '', apiKey: '', proxy: '', language: '', imageBase: '' })
function srcText(src) {
  return { db: '库', env: '环境变量', default: '默认', unset: '未设' }[src] || ''
}
function syncTmdbForm() {
  tmdbForm.value.proxy = s.value?.tmdb_proxy ?? ''
  tmdbForm.value.language = s.value?.tmdb_language ?? ''
  tmdbForm.value.imageBase = s.value?.tmdb_image_base ?? ''
}
async function saveTmdb() {
  const payload = {}
  if (tmdbForm.value.readToken.trim()) payload.tmdb_read_token = tmdbForm.value.readToken.trim()
  if (tmdbForm.value.apiKey.trim()) payload.tmdb_api_key = tmdbForm.value.apiKey.trim()
  if (tmdbForm.value.proxy.trim() !== (s.value?.tmdb_proxy || '')) payload.tmdb_proxy = tmdbForm.value.proxy.trim()
  if (tmdbForm.value.language.trim() !== (s.value?.tmdb_language || '')) payload.tmdb_language = tmdbForm.value.language.trim()
  if (tmdbForm.value.imageBase.trim().replace(/\/+$/, '') !== (s.value?.tmdb_image_base || '')) payload.tmdb_image_base = tmdbForm.value.imageBase.trim()
  if (!Object.keys(payload).length) {
    tmdbCfgMsg.value = '没有改动'
    return
  }
  busy.value = 'tmdb'
  tmdbCfgMsg.value = ''
  try {
    s.value = await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) })
    tmdbForm.value.readToken = ''
    tmdbForm.value.apiKey = ''
    syncTmdbForm()
    await testTmdb()
    tmdbCfgMsg.value = '已保存，' + tmdbMsg.value
  } catch (e) {
    tmdbCfgMsg.value = '保存失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function clearTmdb() {
  if (!armClearTmdb.value) {
    armClearTmdb.value = true
    tmdbCfgMsg.value = '将清空库里的 5 项配置，改回跟随 .env/默认值。再点一次确认执行'
    return
  }
  armClearTmdb.value = false
  busy.value = 'tmdb'
  tmdbCfgMsg.value = ''
  try {
    s.value = await api('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({ tmdb_read_token: '', tmdb_api_key: '', tmdb_proxy: '', tmdb_language: '', tmdb_image_base: '' })
    })
    tmdbForm.value.readToken = ''
    tmdbForm.value.apiKey = ''
    syncTmdbForm()
    tmdbCfgMsg.value = '已恢复跟随 .env'
  } catch (e) {
    tmdbCfgMsg.value = '恢复失败：' + e.message
  } finally {
    busy.value = null
  }
}

const authForm = ref({ token: '' })
const authMsg = ref('')
async function saveAuth() {
  busy.value = 'auth'
  authMsg.value = ''
  try {
    const v = authForm.value.token.trim()
    const d = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ jzmedia_token: v }) })
    s.value = d
    setToken(v)            // 本浏览器后续写操作直接带令牌
    authForm.value.token = ''
    authMsg.value = v ? '已启用写操作鉴权（本浏览器已记住令牌）' : '已关闭鉴权（完全开放）'
  } catch (e) {
    authMsg.value = '保存失败：' + e.message
  } finally {
    busy.value = null
  }
}

const ftsMsg = ref('')
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

const prefs = ref(loadPrefs())
function saveDisplay() {
  savePrefs({ ...prefs.value })
}
function resetDisplay() {
  prefs.value = { ...PREF_DEFAULTS }
  savePrefs({ ...prefs.value })
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
  loadProviders()   // 测试会经过降级链：顺带刷新 provider 冷却状态
}

// 元数据降级链状态（E 阶段补全）：provider 冷却/最近错误 + 重置
const providerInfo = ref(null)
const providers = computed(() => providerInfo.value?.providers || [])
async function loadProviders() {
  try { providerInfo.value = await api('/api/metadata/providers') } catch (e) { /* 忽略 */ }
}
function providerStateText(p) {
  if (!p.available) {
    const s = p.cooldown_remaining || 0
    return s >= 60 ? `冷却中 · 约 ${Math.ceil(s / 60)} 分钟后重试` : `冷却中 · ${s}s 后重试`
  }
  if (p.fails) return `连续失败 ${p.fails} 次`
  if (p.last_ok_at) return '正常'
  return '未使用'
}
async function resetProvider(name) {
  busy.value = 'providers'
  try {
    providerInfo.value = await api('/api/metadata/providers/reset', {
      method: 'POST', body: JSON.stringify({ name }) })
  } catch (e) { /* 忽略 */ }
  finally { busy.value = null }
}

// 库级降级链（P2.5）：视频库为单位配置 provider 顺序（后端 metadata_providers JSON）
const CHAIN_PROVIDERS = ['local', 'tmdb', 'wikidata', 'tvmaze', 'bgm', 'douban', 'nfo']
const CHAIN_LABELS = {
  local: '本地索引', tmdb: 'TMDB', wikidata: 'Wikidata', tvmaze: 'TVmaze',
  bgm: 'Bangumi', douban: '豆瓣(需 env)', nfo: 'NFO 导入'
}
const DEFAULT_CHAIN = ['local', 'tmdb', 'wikidata']
const chainLibId = ref(null)
const chainSel = ref([...DEFAULT_CHAIN])
const chainMsg = ref('')
function loadChainFor() {
  const lib = libList.value.find(l => l.id === chainLibId.value)
  let arr = []
  try { arr = JSON.parse(lib?.metadata_providers || '[]') } catch (e) { arr = [] }
  const valid = Array.isArray(arr) ? arr.filter(x => CHAIN_PROVIDERS.includes(x)) : []
  chainSel.value = valid.length ? valid : [...DEFAULT_CHAIN]
}
function initChain() {
  if (chainLibId.value == null && libList.value.length) {
    chainLibId.value = libList.value[0].id
  }
  loadChainFor()
}
async function saveChain() {
  if (chainLibId.value == null) return
  busy.value = 'chain'
  chainMsg.value = ''
  try {
    const arr = CHAIN_PROVIDERS.filter(n => chainSel.value.includes(n))
    await api('/api/libraries/' + chainLibId.value, {
      method: 'PATCH',
      body: JSON.stringify({ metadata_providers: arr.length ? JSON.stringify(arr) : '' })
    })
    chainMsg.value = arr.length ? `已保存：${arr.join(' → ')}` : '已保存（空=默认链路）'
    await loadLibs(api)
    syncLibs()
  } catch (e) {
    chainMsg.value = '保存失败：' + e.message
  } finally {
    busy.value = null
  }
}

// IMDb 离线数据集导入（P2.5）：后台任务 + 轮询进度
const imdbPath = ref('')
const imdbMsg = ref('')
let imdbTimer = null
async function pollImportImdb(jid) {
  try {
    const d = await api('/api/jobs/import-imdb/' + jid)
    const state = d?.state || 'running'
    if (state === 'done') {
      imdbMsg.value = `导入完成：${d.done || d.total || 0} 条`
      stopImportImdbTimer()
      busy.value = null
    } else if (state === 'failed') {
      imdbMsg.value = '导入失败：' + (d.error || '未知错误')
      stopImportImdbTimer()
      busy.value = null
    } else {
      imdbMsg.value = `导入中… 已处理 ${d.done || 0} 条`
    }
  } catch (e) { /* 轮询失败继续 */ }
}
function stopImportImdbTimer() {
  if (imdbTimer) { clearInterval(imdbTimer); imdbTimer = null }
}
async function doImportImdb() {
  busy.value = 'imdb'
  imdbMsg.value = '启动导入…'
  try {
    const d = await api('/api/jobs/import-imdb', {
      method: 'POST',
      body: JSON.stringify({ path: imdbPath.value.trim() || null })
    })
    imdbMsg.value = d.resumed ? '已有导入任务，继续轮询…' : '导入中…'
    stopImportImdbTimer()
    imdbTimer = setInterval(() => pollImportImdb(d.job_id), 1500)
    pollImportImdb(d.job_id)
  } catch (e) {
    imdbMsg.value = '启动失败：' + e.message
    busy.value = null
  }
}
async function cancelImportImdb() {
  try { await api('/api/jobs/import-imdb/cancel', { method: 'POST', body: '{}' }) } catch (e) { /* 忽略 */ }
  imdbMsg.value = '已取消'
  stopImportImdbTimer()
  busy.value = null
}

async function loadStats() {
  try { stats.value = await api('/api/jobs/stats') } catch (e) { /* 忽略 */ }
}

// 媒体库工具（按视频库标签页）：库列表来自全局状态，库变动后同步
const toolsRef = ref(null)
const libList = ref(listLibs())
const currentId = ref(currentMediaId())
function syncLibs() {
  libList.value = listLibs()
  currentId.value = currentMediaId()
  initChain()
}
async function onLibrariesChanged() {
  syncLibs()
  await loadStats()
}
async function onToolsChanged() {
  await loadStats()
}

const librariesRef = ref(null)

// 左侧导航：只列通用区块 + 一个「媒体库工具」入口（视频库选择在工具区内部 Tab）
const navs = computed(() => [
  { id: 'sec-status', label: '库状态' },
  { id: 'sec-libraries', label: '媒体库' },
  { id: 'sec-tmdb', label: 'TMDB 配置' },
  { id: 'sec-auth', label: '访问控制' },
  { id: 'sec-display', label: '显示' },
  { id: 'sec-index', label: '搜索索引' },
  { id: 'sec-libtools', label: '媒体库工具' },
])
const active = ref('sec-status')
const _sectionLoaded = { 'sec-libraries': false }
function ensureSectionData(id) {
  if (id === 'sec-libraries' && !_sectionLoaded[id]) {
    _sectionLoaded[id] = true
    librariesRef.value?.ensure()
  }
}
function go(n) {
  active.value = n.id
  ensureSectionData(n.id)
  document.getElementById(n.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

let observer = null
onMounted(async () => {
  // 首屏请求并行：设置项 / 库列表 / 统计
  const [settingsResp] = await Promise.all([
    api('/api/settings').catch(() => null),
    loadLibs(api).then(syncLibs).catch(() => null),
    loadStats(),
    loadProviders(),
  ])
  if (settingsResp) { s.value = settingsResp; syncTmdbForm() }
  window.addEventListener('jzmedia:libraries-changed', onLibrariesChanged)

  // 深链承接：?sec=sec-restore&ids=1,2 / ?sec=sec-pipeline&media=N（兼容 ?library=视频库）
  try {
    const q = route.query || {}
    const sec = q.sec ? String(q.sec) : ''
    const ids = (Array.isArray(q.ids) ? q.ids : String(q.ids || '').split(','))
      .map(Number).filter(Number.isFinite)
    const libId = q.library != null && q.library !== '' ? Number(q.library) : null
    const mediaId = q.media != null && q.media !== '' ? Number(q.media) : null
    if (LIB_SECS.has(sec) || ids.length || libId != null || mediaId != null) {
      await toolsRef.value?.focus({
        media: mediaId,
        library: libId,
        sec: LIB_SECS.has(sec) ? sec : '',
        ids,
      })
    }
  } catch (e) { /* 忽略 */ }

  // 通用区块滚动高亮/懒加载（库工具由标签页自行 ensure）
  observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        active.value = e.target.id
        ensureSectionData(e.target.id)
      }
    }
  }, { rootMargin: '-20% 0px -70% 0px' })
  for (const n of navs.value) {
    const el = document.getElementById(n.id)
    if (el) observer.observe(el)
  }
})

onUnmounted(() => {
  if (observer) observer.disconnect()
  window.removeEventListener('jzmedia:libraries-changed', onLibrariesChanged)
})
</script>
<style scoped>
.settings-layout { display: flex; gap: 12px; align-items: flex-start; }
.side-nav { position: sticky; top: 12px; display: flex; flex-direction: column; gap: 6px; min-width: 140px; padding-top: 44px; }
.side-nav button { text-align: left; white-space: nowrap; }
.side-nav button.on { border-color: #e50914; color: #ff8a8a; }
.nav-group { margin-top: 8px; font-size: 0.75rem; color: #888; padding-left: 2px; }
.settings-main { flex: 1; min-width: 0; }
.settings-main section { scroll-margin-top: 12px; }
@media (max-width: 860px) {
  .settings-layout { flex-direction: column; }
  .side-nav { position: static; flex-direction: row; overflow-x: auto; padding-top: 0; min-width: 0; }
  .side-nav button { flex-shrink: 0; }
  .nav-group { display: none; }
}
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.meta-line { color: #aaa; font-size: 0.875rem; margin: 8px 0; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.fhint { font-size: 0.75rem; color: #888; font-weight: normal; }
.warn-text { color: #e0a63c; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; margin: 8px 0; }
.stat { background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 8px 4px; text-align: center; }
.stat b { display: block; font-size: 1.25rem; }
.stat span { color: #888; font-size: 0.75rem; }
.stat.warn b { color: #ff8a8a; }
.slider-row { display: flex; align-items: center; gap: 12px; padding: 6px 12px; }
.slider-row label { min-width: 150px; font-size: 0.875rem; }
.slider-row input[type="range"] { flex: 1; }
.tmdb-grid label { display: block; font-size: 0.875rem; color: #ccc; margin: 8px 0 2px; }
.src-badge { margin-left: 8px; font-size: 0.75rem; color: #888; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; }
.provider-block { margin-top: 12px; border-top: 1px dashed #3a3a3a; padding-top: 8px; }
.provider-block h4 { margin: 0 0 6px; font-size: 0.9375rem; color: #ddd; }
.provider-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 3px 0; font-size: 0.8125rem; }
.chain-item { display: inline-flex; gap: 3px; align-items: center; }
.chain-editor { padding-top: 6px; }
.p-name { min-width: 110px; color: #ccc; }
.p-state { color: #7ed321; }
.p-state.cool { color: #e0a63c; }
.p-err { max-width: 46ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
