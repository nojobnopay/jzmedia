<template>
  <div class="settings-layout">
    <aside class="side-nav" aria-label="设置分类">
      <h1>设置</h1>
      <template v-for="(n, i) in navs" :key="n.id">
        <div v-if="i === 0 || navs[i - 1].group !== n.group" class="nav-group">{{ n.group }}</div>
        <button :class="{ on: active === n.id }" :aria-current="active === n.id ? 'page' : undefined"
          @click="go(n.id)">{{ n.label }}</button>
      </template>
    </aside>
    <main class="settings-main">
      <header class="settings-heading">
        <div><h2>{{ currentPage.label }}</h2><p>{{ currentPage.description }}</p></div>
        <span v-if="busy" class="activity-label" role="status">任务进行中</span>
      </header>
      <p v-if="loadError" class="settings-notice" role="alert">{{ loadError }} <button @click="loadSettings">重新加载</button></p>

      <div v-show="active === 'sec-status'" id="sec-status">
        <section class="card-block">
          <div class="section-heading"><h3>电影库概况</h3><button @click="loadStats">刷新统计</button></div>
          <div v-if="stats" class="stat-grid">
            <div class="stat"><b>{{ stats.grouped }}</b><span>电影</span></div>
            <div class="stat"><b>{{ stats.versions }}</b><span>文件版本</span></div>
            <div class="stat" :class="{ warn: stats.needs_review }"><b>{{ stats.needs_review }}</b><span>匹配待确认</span></div>
            <div class="stat" :class="{ warn: stats.no_match }"><b>{{ stats.no_match }}</b><span>未匹配</span></div>
            <div class="stat" :class="{ warn: stats.missing_files }"><b>{{ stats.missing_files }}</b><span>失效记录</span></div>
          </div>
          <p v-else class="hint" role="status">{{ statsError || '正在加载统计…' }}</p>
          <div v-for="row in (stats?.by_library || []).filter(r => r.pending || r.missing_files)" :key="row.library_id" class="settings-notice">
            <span>{{ libraryName(row.library_id) }}：{{ row.pending }} 项匹配待办 · {{ row.missing_files }} 个失效文件</span>
            <router-link :to="{ path: '/settings', query: { sec: row.pending ? 'sec-pending' : 'sec-sync', library: row.library_id } }">处理待办</router-link>
          </div>
          <details v-if="stats" class="settings-details">
            <summary>存储与缓存统计</summary>
            <dl class="info-grid"><dt>数据库</dt><dd>{{ fmtBytes(stats.db_bytes) }}</dd><dt>海报文件</dt><dd>{{ fmtBytes(stats.posters_bytes) }}</dd><dt>资料缓存</dt><dd>{{ stats.tmdb_cache }} 条</dd><dt>人物</dt><dd>{{ stats.persons }} 位</dd></dl>
          </details>
        </section>
        <section class="card-block">
          <h3>剧集库概况</h3>
          <div v-if="tvStats" class="stat-grid">
            <div class="stat"><b>{{ tvStats.shows }}</b><span>剧集</span></div>
            <div class="stat"><b>{{ tvStats.seasons }}</b><span>季</span></div>
            <div class="stat"><b>{{ tvStats.episodes }}</b><span>分集文件</span></div>
            <div class="stat" :class="{ warn: tvStats.pending }"><b>{{ tvStats.pending }}</b><span>待处理剧集</span></div>
            <div class="stat" :class="{ warn: tvStats.episode_review }"><b>{{ tvStats.episode_review }}</b><span>分集待确认</span></div>
          </div>
          <p v-else class="hint" role="status">{{ tvStatsError || '正在加载统计…' }}</p>
          <div v-for="row in (tvStats?.by_library || []).filter(r => r.pending)" :key="row.library_id" class="settings-notice">
            <span>{{ libraryName(row.library_id) }}：{{ row.pending }} 部剧待处理</span>
            <router-link :to="{ path: '/settings', query: { sec: 'sec-pending', library: row.library_id } }">处理剧集待办</router-link>
          </div>
        </section>
        <div class="settings-shortcuts">
          <button @click="go('sec-libraries')"><b>媒体库连接</b><span>添加本地目录或 NAS，检查连接状态</span></button>
          <button @click="go('sec-libtools')"><b>扫描与整理</b><span>导入新文件，核对匹配，整理目录</span></button>
        </div>
      </div>

      <div v-if="visited.has('sec-libraries')" v-show="active === 'sec-libraries'">
        <LibrariesPanel ref="librariesRef" @changed="onLibrariesChanged" />
      </div>

      <div v-show="active === 'sec-tmdb'" id="sec-tmdb">
        <section class="card-block">
          <div class="section-heading"><h3>TMDB 连接</h3><span class="status-label">{{ s?.tmdb_configured ? '已配置凭据' : '未配置凭据' }}</span></div>
          <div class="settings-form">
            <label for="tmdb-token">读取令牌（Read Token） <span class="fhint">{{ s?.tmdb_read_token_masked || '未设置' }}</span></label>
            <input id="tmdb-token" v-model="tmdbForm.readToken" type="password" placeholder="粘贴新的令牌；留空保留现有令牌" autocomplete="off" />
            <details class="settings-details">
              <summary>使用 API Key</summary>
              <label for="tmdb-key">API Key <span class="fhint">{{ s?.tmdb_api_key_masked || '未设置' }}</span></label>
              <input id="tmdb-key" v-model="tmdbForm.apiKey" type="password" placeholder="没有 Read Token 时使用；留空保留现有密钥" autocomplete="off" />
            </details>
            <div class="form-columns">
              <label>资料语言<input v-model="tmdbForm.language" placeholder="zh-CN" /></label>
              <label>网络代理<input v-model="tmdbForm.proxy" placeholder="http://服务器:端口" autocomplete="off" /></label>
            </div>
            <details class="settings-details">
              <summary>图片地址与配置来源</summary>
              <label for="tmdb-images">图片服务地址</label><input id="tmdb-images" v-model="tmdbForm.imageBase" placeholder="https://image.tmdb.org" />
              <p class="hint">在此保存的配置优先于服务器环境配置，立即生效。清空代理或图片地址会恢复服务器配置或默认值。</p>
              <dl class="info-grid"><dt>读取令牌</dt><dd>{{ srcText(s?.tmdb_read_token_source) }}</dd><dt>API Key</dt><dd>{{ srcText(s?.tmdb_api_key_source) }}</dd><dt>代理</dt><dd>{{ srcText(s?.tmdb_proxy_source) }}</dd><dt>语言</dt><dd>{{ srcText(s?.tmdb_language_source) }}</dd><dt>图片地址</dt><dd>{{ srcText(s?.tmdb_image_base_source) }}</dd></dl>
              <button @click="clearTmdb" :disabled="!!busy || !s">{{ armClearTmdb ? '确认恢复服务器配置' : '恢复服务器配置' }}</button>
              <button v-if="armClearTmdb" @click="armClearTmdb = false">取消恢复</button>
              <p v-if="armClearTmdb" class="hint warn-text">将移除在此保存的五项 TMDB 配置，改用服务器环境配置或默认值。</p>
            </details>
          </div>
          <div class="bar"><button class="primary" @click="saveTmdb" :disabled="!!busy || !s">{{ busy === 'tmdb' ? '保存中…' : '保存 TMDB 配置' }}</button><button @click="testTmdb" :disabled="!!busy || testingTmdb">{{ testingTmdb ? '测试中…' : '测试资料搜索' }}</button></div>
          <p v-if="tmdbCfgMsg || tmdbMsg" class="feedback" role="status">{{ tmdbCfgMsg || tmdbMsg }}</p>
        </section>
        <section class="card-block">
          <h3>匹配来源</h3>
          <p class="hint">按下方顺序查找影片资料，可为每个视频库分别设置。</p>
          <div v-if="libList.length" class="settings-form">
            <label for="source-library">视频库</label>
            <select id="source-library" v-model.number="chainLibId" @change="loadChainFor"><option v-for="l in libList" :key="l.id" :value="l.id">{{ l.media_name ? l.media_name + ' · ' : '' }}{{ l.name }}</option></select>
            <div class="source-options"><label v-for="name in CHAIN_PROVIDERS" :key="name"><input type="checkbox" :value="name" v-model="chainSel" />{{ CHAIN_LABELS[name] }}</label></div>
            <p class="hint">{{ chainSel.length ? chainOrderText : '至少选择一个来源。' }}</p>
            <div class="bar"><button @click="saveChain" :disabled="!!busy || chainLibId == null || !chainSel.length">保存匹配来源</button><button @click="chainSel = [...DEFAULT_CHAIN]">使用默认选择</button></div>
            <p v-if="chainMsg" class="feedback" role="status">{{ chainMsg }}</p>
          </div>
          <p v-else class="hint">添加视频库后即可设置匹配来源。<button @click="go('sec-libraries')">添加媒体库</button></p>
          <details class="settings-details">
            <summary>来源运行状态<span v-if="unavailableProviders" class="warn-text"> · {{ unavailableProviders }} 个来源暂不可用</span></summary>
            <p class="hint">连续失败 {{ providerInfo?.fail_threshold ?? 3 }} 次后暂停使用，约 {{ Math.round((providerInfo?.cooldown_sec ?? 600) / 60) }} 分钟后重试。</p>
            <div class="provider-row" v-for="p in providers" :key="p.name">
              <span class="p-name">{{ p.label }}</span><span :class="{ 'warn-text': !p.available }">{{ providerStateText(p) }}</span>
              <button v-if="!p.available" @click="resetProvider(p.name)" :disabled="!!busy">立即允许重试</button>
              <details v-if="p.last_error" class="provider-error"><summary>错误详情</summary><p>{{ p.last_error }}</p></details>
            </div>
            <p v-if="providerMsg" class="feedback" role="status">{{ providerMsg }}</p>
          </details>
        </section>
        <section class="card-block">
          <details class="settings-details standalone">
            <summary>IMDb 离线数据</summary>
            <p class="hint">用于离线查找标题、年份与 IMDb 编号。先将 title.basics.tsv 或 .gz 文件放到服务器可读目录。</p>
            <label class="field-label" for="imdb-path">服务器文件路径</label><input id="imdb-path" v-model="imdbPath" class="wide-input" placeholder="留空使用服务器预设路径" />
            <div class="bar"><button @click="doImportImdb" :disabled="!!busy">{{ busy === 'imdb' ? '导入中…' : '导入离线数据' }}</button><button v-if="busy === 'imdb'" @click="cancelImportImdb">取消导入</button></div>
          </details>
          <p v-if="imdbMsg" class="feedback" role="status">{{ imdbMsg }}</p>
        </section>
      </div>

      <section v-show="active === 'sec-auth'" id="sec-auth" class="card-block">
        <div class="section-heading"><h3>写操作保护</h3><span class="status-label">{{ s?.jzmedia_token_masked ? '已启用' : '未启用' }}</span></div>
        <p class="hint">启用后，修改内容需要令牌；浏览、播放和媒体直链仍可直接访问。</p>
        <p v-if="s?.jzmedia_token_source === 'env'" class="hint">当前令牌由服务器配置提供。如需关闭保护，请在服务器移除 JZMEDIA_TOKEN；在此可保存新令牌替换。</p>
        <form class="settings-form" @submit.prevent="saveAuth()">
          <label for="access-token">{{ s?.jzmedia_token_masked ? '更换访问令牌' : '设置访问令牌' }}</label>
          <input id="access-token" v-model="authForm.token" type="password" minlength="8" required placeholder="输入至少 8 位字符" autocomplete="new-password" />
          <div class="bar"><button class="primary" type="submit" :disabled="!!busy || !s || authForm.token.trim().length < 8">{{ busy === 'auth' ? '保存中…' : '保存并启用保护' }}</button><button v-if="s?.jzmedia_token_source === 'db'" type="button" @click="armDisableAuth = true" :disabled="!!busy">移除已保存令牌…</button></div>
        </form>
        <div v-if="armDisableAuth" class="settings-notice" role="alert"><span>将移除此处保存的令牌。若服务器仍配置了令牌，将恢复使用；否则关闭保护，任何能访问服务的人都可以修改内容。</span><button class="danger" @click="saveAuth(true)" :disabled="!!busy">确认移除令牌</button><button @click="armDisableAuth = false" :disabled="!!busy">取消</button></div>
        <p v-if="authMsg" class="feedback" role="status">{{ authMsg }}</p>
        <details class="settings-details"><summary>其他浏览器如何使用</summary><p class="hint">其他浏览器首次修改内容时会要求输入令牌，并在该浏览器中记住。此处保存新令牌后，当前浏览器会自动更新。</p></details>
      </section>

      <section v-show="active === 'sec-display'" id="sec-display" class="card-block">
        <h3>显示偏好</h3>
        <div class="slider-row"><label for="font-size">字体大小 <b>{{ prefs.fontSize }} px</b></label><input id="font-size" type="range" min="13" max="20" step="1" v-model.number="prefs.fontSize" @input="saveDisplay" /></div>
        <div class="slider-row"><label for="poster-size">海报大小 <b>{{ prefs.posterMin }} px</b></label><input id="poster-size" type="range" min="120" max="200" step="10" v-model.number="prefs.posterMin" @input="saveDisplay" /><span class="hint">越小，每行显示越多</span></div>
        <div class="bar"><button @click="resetDisplay">恢复默认显示</button><span v-if="displayMsg" role="status">{{ displayMsg }}</span></div>
      </section>

      <div v-show="active === 'sec-index'" id="sec-index">
        <section class="card-block"><h3>搜索索引</h3><p class="hint">搜索结果缺失或与资料不一致时，可重建全部媒体库的搜索索引。</p><div class="bar"><button @click="doRebuildFts" :disabled="!!busy">{{ busy === 'fts' ? '重建中…' : '重建搜索索引' }}</button></div><p v-if="ftsMsg" class="feedback" role="status">{{ ftsMsg }}</p></section>
        <TranscodeCachePanel />
      </div>

      <div v-if="visited.has('sec-libtools')" v-show="active === 'sec-libtools'">
        <LibraryToolsPanel ref="toolsRef" :libs="libList" :current-media-id="currentId" :active="active === 'sec-libtools'" @changed="onToolsChanged" @add-library="go('sec-libraries')" />
      </div>
    </main>
  </div>
</template>
<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import LibrariesPanel from '../components/LibrariesPanel.vue'
import LibraryToolsPanel from '../components/LibraryToolsPanel.vue'
import { fmtBytes } from '../format.js'
import { api, setToken } from '../api.js'
import { currentMediaId, listLibs, loadLibs } from '../libraries.js'
import { loadPrefs, savePrefs, PREF_DEFAULTS } from '../prefs.js'

import TranscodeCachePanel from '../components/TranscodeCachePanel.vue'
import { SETTINGS_PAGES, settingsTarget } from '../settingsNavigation.js'
import '../styles/settings.css'

const route = useRoute()
const router = useRouter()

const s = ref(null)
const stats = ref(null)
const tvStats = ref(null)
const tvStatsError = ref('')
const busy = ref(null) // tmdb|auth|fts

const tmdbMsg = ref('')
const tmdbCfgMsg = ref('')
const armClearTmdb = ref(false)
const tmdbForm = ref({ readToken: '', apiKey: '', proxy: '', language: '', imageBase: '' })
function srcText(src) {
  return { db: '设置页', env: '服务器环境配置', default: '系统默认', unset: '未设置' }[src] || ''
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
    tmdbCfgMsg.value = 'TMDB 配置已保存'
    tmdbMsg.value = ''
  } catch (e) {
    tmdbCfgMsg.value = '保存失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function clearTmdb() {
  if (!armClearTmdb.value) {
    armClearTmdb.value = true
    tmdbCfgMsg.value = ''
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
    tmdbCfgMsg.value = '已恢复服务器配置或默认值'
  } catch (e) {
    tmdbCfgMsg.value = '恢复失败：' + e.message
  } finally {
    busy.value = null
  }
}

const authForm = ref({ token: '' })
const authMsg = ref('')
const armDisableAuth = ref(false)
async function saveAuth(disable = false) {
  if (busy.value || (!disable && authForm.value.token.trim().length < 8)) return
  busy.value = 'auth'
  authMsg.value = ''
  try {
    const v = disable ? '' : authForm.value.token.trim()
    const d = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ jzmedia_token: v }) })
    s.value = d
    setToken(v)            // 本浏览器后续写操作直接带令牌
    authForm.value.token = ''
    armDisableAuth.value = false
    authMsg.value = v ? '已启用保护，当前浏览器已记住令牌'
      : d.jzmedia_token_masked ? '已恢复服务器令牌，下次修改时需要重新输入' : '已关闭写操作保护'
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
const displayMsg = ref('')
function saveDisplay() {
  savePrefs({ ...prefs.value })
  displayMsg.value = '已保存到当前浏览器'
}
function resetDisplay() {
  prefs.value = { ...PREF_DEFAULTS }
  savePrefs({ ...prefs.value })
  displayMsg.value = '已恢复默认显示'
}

const testingTmdb = ref(false)
async function testTmdb() {
  testingTmdb.value = true
  tmdbCfgMsg.value = ''
  tmdbMsg.value = '测试中…'
  const t0 = performance.now()
  try {
    await api('/api/tmdb/search?q=' + encodeURIComponent('阿凡达'))
    tmdbMsg.value = `资料搜索可用（${Math.round(performance.now() - t0)} ms，可能使用本地或备用来源）`
  } catch (e) {
    tmdbMsg.value = '资料搜索失败：' + e.message
  }
  testingTmdb.value = false
  loadProviders()   // 测试会经过降级链：顺带刷新 provider 冷却状态
}

// 元数据降级链状态（E 阶段补全）：provider 冷却/最近错误 + 重置
const providerInfo = ref(null)
const providerMsg = ref('')
const unavailableProviders = computed(() => providers.value.filter(p => !p.available).length)
const providers = computed(() => providerInfo.value?.providers || [])
async function loadProviders() {
  try { providerInfo.value = await api('/api/metadata/providers') } catch (e) { providerMsg.value = '状态加载失败：' + e.message }
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
  providerMsg.value = ''
  try {
    providerInfo.value = await api('/api/metadata/providers/reset', {
      method: 'POST', body: JSON.stringify({ name }) })
  } catch (e) { providerMsg.value = '操作失败：' + e.message }
  finally { busy.value = null }
}

// 库级降级链（P2.5）：视频库为单位配置 provider 顺序（后端 metadata_providers JSON）
const CHAIN_PROVIDERS = ['local', 'tmdb', 'wikidata', 'tvmaze', 'bgm', 'douban', 'nfo']
const CHAIN_LABELS = {
  local: '本地索引', tmdb: 'TMDB', wikidata: 'Wikidata', tvmaze: 'TVmaze',
  bgm: 'Bangumi', douban: '豆瓣（需服务器配置）', nfo: 'NFO 导入'
}
const DEFAULT_CHAIN = ['local', 'tmdb', 'wikidata']
const chainLibId = ref(null)
const chainSel = ref([...DEFAULT_CHAIN])
const chainMsg = ref('')
const chainOrderText = computed(() => CHAIN_PROVIDERS.filter(n => chainSel.value.includes(n)).map(n => CHAIN_LABELS[n]).join(' → '))
function loadChainFor() {
  chainMsg.value = ''
  const lib = libList.value.find(l => l.id === chainLibId.value)
  let arr = []
  try { arr = JSON.parse(lib?.metadata_providers || '[]') } catch (e) { arr = [] }
  const valid = Array.isArray(arr) ? arr.filter(x => CHAIN_PROVIDERS.includes(x)) : []
  chainSel.value = valid.length ? valid : [...DEFAULT_CHAIN]
}
function initChain() {
  if (!libList.value.some(l => l.id === chainLibId.value) && libList.value.length) {
    chainLibId.value = libList.value[0].id
  }
  loadChainFor()
}
async function saveChain() {
  if (chainLibId.value == null || !chainSel.value.length) return
  busy.value = 'chain'
  chainMsg.value = ''
  try {
    const arr = CHAIN_PROVIDERS.filter(n => chainSel.value.includes(n))
    await api('/api/libraries/' + chainLibId.value, {
      method: 'PATCH',
      body: JSON.stringify({ metadata_providers: arr.length ? JSON.stringify(arr) : '' })
    })
    await loadLibs(api)
    syncLibs()
    chainMsg.value = `已保存：${arr.map(n => CHAIN_LABELS[n]).join(' → ')}`
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
    } else if (state === 'cancelled') {
      imdbMsg.value = '导入已取消，已导入的数据保留'
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
  try {
    await api('/api/jobs/import-imdb/cancel', { method: 'POST', body: '{}' })
    imdbMsg.value = '已请求取消，正在等待任务停止…'
  } catch (e) { imdbMsg.value = '取消失败：' + e.message }
}

const statsError = ref('')
function libraryName(id) {
  const lib = libList.value.find(l => Number(l.id) === Number(id))
  return lib ? `${lib.media_name ? lib.media_name + ' / ' : ''}${lib.name}` : `视频库 ${id}`
}
async function loadStats() {
  statsError.value = ''
  tvStatsError.value = ''
  await Promise.all([
    api('/api/jobs/stats').then(d => { stats.value = d }).catch(e => { statsError.value = '统计加载失败：' + e.message }),
    api('/api/tv/stats').then(d => { tvStats.value = d }).catch(e => { tvStatsError.value = '统计加载失败：' + e.message }),
  ])
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

// Panels stay mounted after first use so switching pages retains forms and job progress.
const navs = SETTINGS_PAGES
const active = ref(settingsTarget(route.query).page)
const currentPage = computed(() => navs.find(n => n.id === active.value) || navs[0])
const visited = ref(new Set([active.value]))
const ready = ref(false)
const loadError = ref('')
let navigationGeneration = 0
async function activate(query) {
  const generation = ++navigationGeneration
  const target = settingsTarget(query)
  active.value = target.page
  visited.value.add(target.page)
  await nextTick()
  if (generation !== navigationGeneration) return
  if (target.page === 'sec-libraries') librariesRef.value?.ensure()
  if (target.page === 'sec-libtools' && ready.value) {
    await toolsRef.value?.focus(target)
  }
}
async function go(id) {
  const query = { ...route.query, sec: id }
  for (const key of ['ids', 'library', 'media']) delete query[key]
  await router.push({ path: '/settings', query })
  window.scrollTo({ top: 0 })
}
watch(() => route.query, activate)

async function loadSettings() {
  loadError.value = ''
  try {
    s.value = await api('/api/settings')
    syncTmdbForm()
  } catch (e) { loadError.value = '设置加载失败：' + e.message }
}
onMounted(async () => {
  window.addEventListener('jzmedia:libraries-changed', onLibrariesChanged)
  await Promise.all([
    loadSettings(),
    loadLibs(api).then(syncLibs).catch(e => { loadError.value = '媒体库加载失败：' + e.message }),
    loadStats(), loadProviders(),
  ])
  ready.value = true
  await activate(route.query)
})
onUnmounted(() => {
  navigationGeneration++
  stopImportImdbTimer()
  window.removeEventListener('jzmedia:libraries-changed', onLibrariesChanged)
})
</script>
