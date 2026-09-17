<template>
  <div class="settings-layout">
    <aside class="side-nav">
      <button v-for="n in navs" :key="n.id" :class="{ on: active === n.id }" @click="go(n.id)">
        {{ n.label }}<span v-if="n.badge" class="nav-badge">{{ n.badge }}</span>
      </button>
    </aside>
    <div class="page settings-main">
      <h2>设置</h2>

      <section id="sec-status" class="card-block">
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

      <section id="sec-pipeline" class="card-block">
        <h3>入库流程</h3>
        <p class="hint">按顺序走：① 扫描新增 → ② 解决未匹配/待确认 → ③ 归档到正式库。库级批量修复（补产地/刷新 TMDB/NFO/搜索索引）见下方「高级维护」。</p>
        <div id="sec-sync" class="pipe-step">
          <div class="pipe-head">
            <h4>① 扫描入库</h4>
            <span class="fhint">NAS 直拷 / 软件外删片后用：先扫描新增，再检查并清理失效条目</span>
          </div>
          <div class="pipe-body">
        <div class="bar">
          <button @click="doScan" :disabled="!!busy">{{ busy === 'scan' ? '扫描中…' : '扫描新文件' }}</button>
          <button v-if="busy === 'scan'" @click="cancelScan">取消</button>
          <span>{{ scanMsg }}</span>
        </div>
        <div class="bar">
          <button @click="loadMissing" :disabled="!!busy">检查失效条目</button>
          <button v-if="missing.length" @click="toggleAllMissing">{{ allChecked ? '全不选' : '全选' }}</button>
          <span v-if="missing.length">共 {{ missing.length }} 条失效</span>
          <button v-if="missing.length > COLLAPSE_N" @click="showAllMissing = !showAllMissing">{{ showAllMissing ? '收起' : `展开全部 (${missing.length})` }}</button>
        </div>
        <ul v-if="missing.length" class="miss-list">
          <li v-for="m in visibleMissing" :key="m.id" class="miss-row">
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
          </div>
        </div>
        <div id="sec-pending" class="pipe-step">
          <div class="pipe-head pipe-toggle" @click="pipeOpen.pending = !pipeOpen.pending">
            <h4>② 待匹配确认 <span v-if="pendingCount" class="nav-badge">{{ pendingCount }}</span></h4>
            <span class="fhint">{{ pipeOpen.pending ? '收起' : '展开' }}</span>
          </div>
          <div v-show="pipeOpen.pending" class="pipe-body">
        <p class="hint">上传/扫描后没认出来的片在这里核对。未匹配：TMDB 没找到数据；待确认：模糊命中需人工核对；疑似英文标题：非英语片却显示英文（错配或缺翻译）；未归属花絮：对不上任何影片。点「去处理」到详情页手动绑定。</p>
        <div class="bar">
          <button @click="loadUnmatched" :disabled="!!busy">刷新</button>
          <span v-if="pendingTotal">未匹配 {{ unmatched.length }} · 待确认 {{ needsReview.length }} · 疑似英文 {{ suspectHigh.length + suspectInfo.length }} · 未归属花絮 {{ orphans.length }}</span>
          <span v-else>全部已匹配</span>
        </div>
        <h4 v-if="unmatched.length" class="sub-h">未匹配（{{ unmatched.length }}）<button v-if="unmatched.length > COLLAPSE_N" @click="showAllUnmatched = !showAllUnmatched">{{ showAllUnmatched ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="unmatched.length" class="miss-list">
          <li v-for="m in visibleUnmatched" :key="'u' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="needsReview.length" class="sub-h">待确认（{{ needsReview.length }}）<button v-if="needsReview.length > COLLAPSE_N" @click="showAllNeedsReview = !showAllNeedsReview">{{ showAllNeedsReview ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="needsReview.length" class="miss-list">
          <li v-for="m in visibleNeedsReview" :key="'n' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="suspectHigh.length" class="sub-h">疑似英文标题·重点看（{{ suspectHigh.length }}）<button v-if="suspectHigh.length > COLLAPSE_N" @click="showAllSuspectHigh = !showAllSuspectHigh">{{ showAllSuspectHigh ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="suspectHigh.length" class="miss-list">
          <li v-for="m in visibleSuspectHigh" :key="'sh' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="suspectInfo.length" class="sub-h">英文标题·信息（{{ suspectInfo.length }}，英语片多为正常，刷新后复看）<button v-if="suspectInfo.length > COLLAPSE_N" @click="showAllSuspectInfo = !showAllSuspectInfo">{{ showAllSuspectInfo ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="suspectInfo.length" class="miss-list">
          <li v-for="m in visibleSuspectInfo" :key="'si' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="orphans.length" class="sub-h">未归属花絮（{{ orphans.length }}，文件原地保留，填影片ID认领）<button v-if="orphans.length > COLLAPSE_N" @click="showAllOrphans = !showAllOrphans">{{ showAllOrphans ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="orphans.length" class="miss-list">
          <li v-for="e in visibleOrphans" :key="'o' + e.id" class="miss-row">
            <span class="miss-title">{{ e.kind }}</span>
            <span class="miss-path">{{ e.file_path }}<span v-if="e.guessed_title" class="fhint">（猜测：{{ e.guessed_title }}{{ e.guessed_year ? ' ' + e.guessed_year : '' }}）</span></span>
            <input v-model="orphanMovie[e.id]" placeholder="影片ID" style="width:80px" />
            <button @click="attachOrphan(e.id)" :disabled="!!busy">认领</button>
          </li>
        </ul>
        <div class="bar">
          <button @click="doCollectExtras" :disabled="!!busy">{{ busy === 'collect' ? '归位中…' : (armCollect ? '确认归位花絮' : '归位已归属花絮到各片 extras/') }}</button>
          <span>{{ collectMsg }}</span>
        </div>
          </div>
        </div>
        <OrganizePanel ref="organizeRef" @changed="onOrganizeChanged" />
      </section>


      <section id="sec-meta" class="card-block">
        <h3>高级维护</h3>
        <p class="hint">库级批量修复，日常无需操作：补产地信息、逐部刷新 TMDB、重建 NFO、重建搜索索引。</p>
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

      <RestorePanel ref="restoreRef" @count="restoreCount = $event" @changed="onPanelChanged" />

      <FsBrowser :active="active === 'sec-files'" @changed="loadStats" @scan="doScan" />
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import FsBrowser from '../components/FsBrowser.vue'
import OrganizePanel from '../components/OrganizePanel.vue'
import RestorePanel from '../components/RestorePanel.vue'
import { fmtBytes } from '../format.js'
import { api, setToken } from '../api.js'
import { usePolling } from '../usePolling.js'
import { loadPrefs, savePrefs, PREF_DEFAULTS } from '../prefs.js'

const route = useRoute()

const COLLAPSE_N = 20

const s = ref(null)
const stats = ref(null)
const busy = ref(null) // scan|clean|backfill|refresh|nfo|fts|organize|restore|tmdb

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
const scanMsg = ref('')
const cleanMsg = ref('')
const backfillMsg = ref('')
const refreshMsg = ref('')
const nfoMsg = ref('')
const ftsMsg = ref('')

const organizeRef = ref(null)
const restoreRef = ref(null)

const missing = ref([])
const checkedMissing = ref([])
const allChecked = computed(() => missing.value.length > 0 && checkedMissing.value.length === missing.value.length)
const showAllMissing = ref(false)
const visibleMissing = computed(() => showAllMissing.value ? missing.value : missing.value.slice(0, COLLAPSE_N))

const unmatched = ref([])
const needsReview = ref([])
const suspectHigh = ref([])
const suspectInfo = ref([])
const orphans = ref([])
const orphanMovie = ref({})
const collectMsg = ref('')
const showAllUnmatched = ref(false)
const showAllNeedsReview = ref(false)
const showAllSuspectHigh = ref(false)
const showAllSuspectInfo = ref(false)
const showAllOrphans = ref(false)
const visibleUnmatched = computed(() => showAllUnmatched.value ? unmatched.value : unmatched.value.slice(0, COLLAPSE_N))
const visibleNeedsReview = computed(() => showAllNeedsReview.value ? needsReview.value : needsReview.value.slice(0, COLLAPSE_N))
const visibleSuspectHigh = computed(() => showAllSuspectHigh.value ? suspectHigh.value : suspectHigh.value.slice(0, COLLAPSE_N))
const visibleSuspectInfo = computed(() => showAllSuspectInfo.value ? suspectInfo.value : suspectInfo.value.slice(0, COLLAPSE_N))
const visibleOrphans = computed(() => showAllOrphans.value ? orphans.value : orphans.value.slice(0, COLLAPSE_N))
const pendingTotal = computed(() => unmatched.value.length + needsReview.value.length + suspectHigh.value.length + orphans.value.length)

const prefs = ref(loadPrefs())

// 左侧悬浮导航
const pendingCount = computed(() => pendingTotal.value)
const restoreCount = ref(0)
const navs = computed(() => [
  { id: 'sec-status', label: '库状态' },
  { id: 'sec-tmdb', label: 'TMDB 配置' },
  { id: 'sec-auth', label: '访问控制' },
  { id: 'sec-pipeline', label: '入库流程', badge: pendingCount.value || '' },
  { id: 'sec-meta', label: '高级维护' },
  { id: 'sec-display', label: '显示' },
  { id: 'sec-restore', label: '恢复原始位置', badge: restoreCount.value || '' },
  { id: 'sec-files', label: '文件浏览' },
])
const active = ref('sec-status')
const pipeOpen = ref({ pending: true })
let observer = null
const _sectionLoaded = { 'sec-sync': false, 'sec-pending': false, 'sec-pipeline': false, 'sec-restore': false }
function ensureSectionData(id) {
  // 重负载清单按需加载（评审 B8/R09-Q5）：首屏不打 missing/unmatched 全表扫描
  if (id === 'sec-sync' && !_sectionLoaded[id]) {
    _sectionLoaded[id] = true
    loadMissing(true)
  } else if (id === 'sec-pending' && !_sectionLoaded[id]) {
    _sectionLoaded[id] = true
    loadUnmatched(true)
  } else if (id === 'sec-restore' && !_sectionLoaded[id]) {
    // 恢复面板进入区块自动加载（H-UI：原「预览」按钮仅做首次加载，已删）
    _sectionLoaded[id] = true
    restoreRef.value?.ensure()
  } else if (id === 'sec-pipeline') {
    // 合并后的「入库流程」：两个重负载清单一起按需加载（评审 P2 后续）
    ensureSectionData('sec-sync')
    ensureSectionData('sec-pending')
    if (!_sectionLoaded['sec-pipeline']) {
      // 只做一次（观察器会在滚动中反复触发）；归档预览由 OrganizePanel 自行懒加载
      _sectionLoaded['sec-pipeline'] = true
      organizeRef.value?.ensure(true)
    }
  }
}
function go(id) {
  active.value = id
  ensureSectionData(id)
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
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

// 扫描后台任务（评审 B9/R04-D6）：轮询进度，可取消
let scanJobId = ''
const scanPoll = usePolling(pollScanJob, { interval: 1000 })
function finishScanJob() {
  scanPoll.stop()
  scanJobId = ''
  busy.value = null
}
async function doScan() {
  busy.value = 'scan'
  scanMsg.value = ''
  try {
    const d = await api('/api/jobs/scan', { method: 'POST' })
    scanJobId = d.job_id
    if (d.resumed) scanMsg.value = '已有扫描在跑，跟踪进度…'
    scanPoll.start()
  } catch (e) {
    scanMsg.value = '扫描启动失败：' + e.message
    busy.value = null
  }
}
async function pollScanJob() {
  if (!scanJobId) return
  try {
    const st = await api('/api/jobs/scan/' + scanJobId)
    if (st.state === 'running') {
      if (st.total) scanMsg.value = `刮削中 ${st.done}/${st.total}…`
      return
    }
    if (st.state === 'done') {
      const sum = st.summary || {}
      const c = sum.counts || {}
      const ok = (c.ok || 0) + (c.ok_needs_review || 0)
      scanMsg.value = `完成：新增/更新 ${ok}，已同步跳过 ${c.skipped_cached || 0}，未匹配 ${c.no_match || 0}`
        + (c.scan_failed ? `，刮削失败 ${c.scan_failed}（可重试）` : '')
        + (c.skipped_episode_v1 ? `，剧集跳过 ${c.skipped_episode_v1}` : '')
        + ((sum.errors || []).length ? `，失败 ${sum.errors.length}` : '')
    } else if (st.state === 'cancelled') {
      scanMsg.value = `已取消（${st.done}/${st.total}）`
    } else {
      scanMsg.value = '扫描失败：' + (st.error || '未知')
    }
    finishScanJob()
    await loadStats()
    ensureSectionData('sec-sync')
    ensureSectionData('sec-pending')
    // 扫描入库后主动提示归档（评审 B9 后续）：有可归档项就展开 ③ 并说明下一步
    organizeRef.value?.refresh(true)
  } catch (e) { /* 轮询失败下次继续 */ }
}
async function cancelScan() {
  if (!scanJobId) return
  try { await api('/api/jobs/scan/' + scanJobId + '/cancel', { method: 'POST' }) } catch (e) { /* 忽略 */ }
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
    const d = await api('/api/files/clean', { method: 'POST', body: JSON.stringify({ ids: checkedMissing.value, dry_run: false }) })
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

async function loadUnmatched(silent) {
  try {
    const d = await api('/api/files/unmatched')
    unmatched.value = d.unmatched || []
    needsReview.value = d.needs_review || []
    suspectHigh.value = d.suspect_title_high || []
    suspectInfo.value = d.suspect_title_info || []
    orphans.value = d.orphan_extras || []
  } catch (e) {
    if (!silent) scanMsg.value = '待处理加载失败：' + e.message
  }
}

async function attachOrphan(id) {
  const mid = Number((orphanMovie.value[id] || '').toString().trim())
  if (!mid) return
  busy.value = 'attach'
  try {
    await api('/api/extras/' + id + '/attach', { method: 'POST', body: JSON.stringify({ movie_id: mid }) })
    orphans.value = orphans.value.filter(e => e.id !== id)
  } catch (e) {
    scanMsg.value = '认领失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armCollect = ref(false)
async function doCollectExtras() {
  if (!armCollect.value) {
    armCollect.value = true
    collectMsg.value = '把已归属但散落在外的花絮搬进各片 extras/。再点一次确认执行'
    return
  }
  armCollect.value = false
  busy.value = 'collect'
  collectMsg.value = ''
  try {
    const prev = await api('/api/extras/collect', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
    if (!prev.total) {
      collectMsg.value = '没有待归位花絮'
      return
    }
    const d = await api('/api/extras/collect', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
    collectMsg.value = `已归位 ${d.moved} 个文件（${d.total} 部片）`
    await loadUnmatched(true)
  } catch (e) {
    collectMsg.value = '归位失败：' + e.message
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

async function onPanelChanged() {
  await loadStats()
  await loadMissing(true)
}
async function onOrganizeChanged() {
  await onPanelChanged()
  // 归档会改变文件路径 → 恢复清单若已加载需刷新（H-UI：原「预览」按钮兼做刷新）
  await restoreRef.value?.reloadIfLoaded()
}

// 文件浏览（直操 MEDIA_ROOT：浏览/建目录/改名/移动/删除，正片二次确认）
onMounted(async () => {
  // 首屏请求并行（评审 B8/R14-D1）：此前 6 个重查询串行，大库首开很慢
  const [settingsResp] = await Promise.all([
    api('/api/settings').catch(() => null),
    loadStats(),
  ])
  if (settingsResp) { s.value = settingsResp; syncTmdbForm() }
  // 两个重负载清单延迟加载（评审 B8/R09-Q5）：滚动到区块时拉；另 4s 空闲补拉徽标数
  setTimeout(() => ensureSectionData('sec-pipeline'), 4000)
  // 详情页“去恢复”跳转承接：?sec=sec-restore&ids=1,2 → 预选并滚动定位
  try {
    const q = route.query || {}
    const ids = (Array.isArray(q.ids) ? q.ids : String(q.ids || '').split(','))
      .map(Number).filter(Number.isFinite)
    if (q.sec || ids.length) await restoreRef.value?.load(ids)
    if (q.sec && document.getElementById(String(q.sec))) {
      active.value = String(q.sec)
      document.getElementById(String(q.sec))?.scrollIntoView({ block: 'start' })
    }
  } catch (e) { /* 忽略 */ }
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
})
</script>
<style scoped>
.settings-layout { display: flex; gap: 12px; align-items: flex-start; }
.side-nav { position: sticky; top: 12px; display: flex; flex-direction: column; gap: 6px; min-width: 140px; padding-top: 44px; }
.side-nav button { text-align: left; white-space: nowrap; }
.side-nav button.on { border-color: #e50914; color: #ff8a8a; }
.nav-badge { margin-left: 6px; font-size: 0.75rem; color: #e0a63c; }
.settings-main { flex: 1; min-width: 0; }
.settings-main section { scroll-margin-top: 12px; }
@media (max-width: 860px) {
  .settings-layout { flex-direction: column; }
  .side-nav { position: static; flex-direction: row; overflow-x: auto; padding-top: 0; min-width: 0; }
  .side-nav button { flex-shrink: 0; }
}
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.pipe-step { margin: 12px 0 0; border-top: 1px dashed #3a3a3a; padding-top: 10px; }
.pipe-head { display: flex; gap: 8px; align-items: baseline; }
.pipe-head h4 { margin: 0; font-size: 0.9375rem; color: #ccc; }
.pipe-toggle { cursor: pointer; user-select: none; }
.pipe-toggle:hover h4 { color: #fff; }
.pipe-body { margin-top: 6px; }
.sub-h { margin: 10px 0 4px; font-size: 0.9375rem; color: #ccc; display: flex; gap: 8px; align-items: center; }
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
.miss-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.tmdb-grid label { display: block; font-size: 0.875rem; color: #ccc; margin: 8px 0 2px; }
.src-badge { margin-left: 8px; font-size: 0.75rem; color: #888; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; }
.miss-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.miss-title { white-space: nowrap; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
</style>
