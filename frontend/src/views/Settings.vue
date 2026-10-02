<template>
  <div class="settings-layout">
    <aside class="side-nav" aria-label="设置导航">
      <h1>设置</h1><router-link class="setup-link" to="/setup">新手配置</router-link>
      <nav class="desktop-categories" aria-label="设置分区">
        <section v-for="(group, i) in navGroups" :key="group.name" :aria-labelledby="'settings-group-' + i">
          <h2 :id="'settings-group-' + i" class="nav-group">{{ group.name }}</h2>
          <button v-for="n in group.pages" :key="n.id" :class="{ on: active === n.id }" :aria-current="active === n.id ? 'page' : undefined"
            @click="go(n.id)">{{ n.label }}</button>
        </section>
      </nav>
      <div class="mobile-categories"><label for="settings-category">设置分类</label>
        <select id="settings-category" :value="active" @change="changeCategory">
          <optgroup v-for="group in navGroups" :key="group.name" :label="group.name">
            <option v-for="n in group.pages" :key="n.id" :value="n.id">{{ n.label }}</option>
          </optgroup>
        </select>
      </div>
    </aside>
    <main class="settings-main">
      <header class="settings-heading">
        <div><h2>{{ currentPage.label }}</h2><p>{{ currentPage.description }}</p><span class="settings-scope">{{ settingsScope }}</span></div>
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
        <TmdbSettingsPanel :settings="s" @saved="s = $event" />
        <section class="card-block">
          <div class="section-heading"><h3>来源运行状态</h3><button @click="loadProviders">刷新状态</button></div>
          <p class="hint">连续失败 {{ providerInfo?.fail_threshold ?? 3 }} 次后暂停使用，约 {{ Math.round((providerInfo?.cooldown_sec ?? 600) / 60) }} 分钟后重试。状态在全部视频库之间共享。</p>
          <p v-if="unavailableProviders" class="warn-text">{{ unavailableProviders }} 个来源暂不可用</p>
          <div class="provider-row" v-for="p in providers" :key="p.name">
            <span class="p-name">{{ p.label }}</span><span :class="{ 'warn-text': !p.available }">{{ providerStateText(p) }}</span>
            <button v-if="!p.available" @click="resetProvider(p.name)" :disabled="!!busy">立即允许重试</button>
            <details v-if="p.last_error" class="provider-error"><summary>错误详情</summary><p>{{ p.last_error }}</p></details>
          </div>
          <p v-if="providerMsg" class="feedback" role="status">{{ providerMsg }}</p>
          <button @click="go('sec-matching')">设置视频库匹配规则</button>
        </section>
      </div>

      <div v-show="active === 'sec-matching'" id="sec-matching">
        <section class="card-block">
          <h3>来源与优先级</h3>
          <p class="hint">依次尝试已启用的来源，找到候选后停止。规则保存后仅对选中的视频库生效。</p>
          <div v-if="libList.length" class="settings-form">
            <label for="source-library">视频库</label>
            <select id="source-library" v-model.number="chainLibId"><option v-for="l in libList" :key="l.id" :value="l.id">{{ l.media_name ? l.media_name + ' · ' : '' }}{{ l.name }} · {{ l.kind === 'tv' ? '剧集' : '电影' }}</option></select>
            <div class="source-options"><label v-for="name in CHAIN_PROVIDERS" :key="name"><input type="checkbox" :checked="chainSel.includes(name)" @change="toggleChain(name, $event.target.checked)" />{{ CHAIN_LABELS[name] }}</label></div>
            <ol class="source-order" aria-label="已启用来源的匹配顺序">
              <li v-for="(name, index) in chainSel" :key="name">
                <span>{{ index + 1 }}. {{ CHAIN_LABELS[name] }}</span>
                <div><button :disabled="index === 0" :aria-label="'上移 ' + CHAIN_LABELS[name]" @click="moveChain(index, -1)">上移</button><button :disabled="index === chainSel.length - 1" :aria-label="'下移 ' + CHAIN_LABELS[name]" @click="moveChain(index, 1)">下移</button></div>
              </li>
            </ol>
            <p class="hint">{{ chainSel.length ? chainOrderText : '至少选择一个来源。' }}</p>
            <p v-if="chainDraftCount" class="hint" role="status">{{ chainDraftCount }} 个视频库有未保存修改。切换视频库保留草稿；离开设置页前请保存。</p>
            <div class="bar"><button class="primary" @click="saveChain" :disabled="savingChain || chainLibId == null || !chainSel.length">{{ savingChain ? '保存中…' : '保存匹配规则' }}</button><button @click="chainSel = [...DEFAULT_CHAIN]">恢复默认选择</button><button v-if="chainDirty" @click="discardChain">放弃本库修改</button></div>
            <p v-if="chainMsg" class="feedback" role="status">{{ chainMsg }}</p>
          </div>
          <p v-else class="hint">添加视频库后即可设置匹配来源。<button @click="go('sec-libraries')">添加媒体库</button></p>
        </section>
        <section v-if="chainLibrary" class="card-block">
          <h3>测试当前视频库匹配</h3>
          <p class="hint">{{ libraryName(chainLibId) }} · {{ chainLibrary.kind === 'tv' ? '剧集' : '电影' }}。使用本库已保存的来源顺序查找候选，不会修改媒体资料。</p>
          <form class="settings-form" @submit.prevent="testChain">
            <label for="matching-query">{{ chainLibrary.kind === 'tv' ? '剧集名称' : '电影名称' }}</label>
            <input id="matching-query" v-model="chainQuery" :placeholder="chainLibrary.kind === 'tv' ? '例如：三体' : '例如：阿凡达'" />
            <p v-if="chainDirty" class="hint">本库规则有修改，请保存后再测试。</p>
            <div class="bar"><button :disabled="chainDirty || savingChain || testingChain || !chainQuery.trim()">{{ testingChain ? '查找中…' : '测试已保存规则' }}</button><button type="button" @click="go('sec-tmdb')">查看来源状态</button></div>
          </form>
          <p v-if="chainTestMsg" class="feedback" role="status">{{ chainTestMsg }}</p>
          <ul v-if="chainTestResult?.items?.length" class="matching-results"><li v-for="(item, index) in chainTestResult.items.slice(0, 5)" :key="index">{{ item.title || item.name }} <span class="hint">{{ item.year || item.release_date?.slice(0, 4) }} · {{ CHAIN_LABELS[item.source] || item.source || '未标注来源' }}</span></li></ul>
        </section>
      </div>

      <div v-if="visited.has('sec-ai')" v-show="active === 'sec-ai'" id="sec-ai"><AiSettingsPanel /></div>

      <section v-show="active === 'sec-offline'" id="sec-offline" class="card-block">
        <h3>IMDb 离线数据</h3>
        <p class="hint">用于离线查找标题、年份与 IMDb 编号。先将 title.basics.tsv 或 .gz 文件放到服务器可读目录；容器部署时填写容器内的挂载路径。</p>
        <label class="field-label" for="imdb-path">服务器文件路径</label><input id="imdb-path" v-model="imdbPath" class="wide-input" placeholder="留空使用服务器预设路径" />
        <div class="bar"><button @click="doImportImdb" :disabled="!!busy">{{ busy === 'imdb' ? '导入中…' : '导入离线数据' }}</button><button v-if="busy === 'imdb'" @click="cancelImportImdb">取消导入</button></div>
        <p v-if="imdbMsg" class="feedback" role="status">{{ imdbMsg }}</p>
        <p class="hint">导入后，在“匹配规则”中启用“本地索引”，即可用于该视频库的资料查找。</p>
      </section>

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

      <div v-if="visited.has('sec-libtools') || visited.has('sec-files')" v-show="active === 'sec-libtools' || active === 'sec-files'">
        <LibraryToolsPanel ref="toolsRef" :libs="libList" :current-media-id="currentId" :libraries-ready="ready" :libraries-error="librariesError"
          :active="active === 'sec-libtools' || active === 'sec-files'" @changed="onToolsChanged" @add-library="go('sec-libraries')" @retry-libraries="loadLibraries(true)" />
      </div>
    </main>
  </div>
</template>
<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TmdbSettingsPanel from '../components/TmdbSettingsPanel.vue'
import AiSettingsPanel from '../components/AiSettingsPanel.vue'
import LibrariesPanel from '../components/LibrariesPanel.vue'
import LibraryToolsPanel from '../components/LibraryToolsPanel.vue'
import { fmtBytes } from '../format.js'
import { api, setToken } from '../api.js'
import { currentMediaId, listLibs, loadLibs } from '../libraries.js'
import { loadPrefs, savePrefs, PREF_DEFAULTS } from '../prefs.js'

import TranscodeCachePanel from '../components/TranscodeCachePanel.vue'
import { SETTINGS_PAGES, settingsTarget } from '../settingsNavigation.js'
import { CHAIN_PROVIDERS, CHAIN_LABELS, DEFAULT_CHAIN, useMatchingSettings } from '../useMatchingSettings.js'
import '../styles/settings.css'

const route = useRoute()
const router = useRouter()

const s = ref(null)
const stats = ref(null)
const tvStats = ref(null)
const tvStatsError = ref('')
const busy = ref(null) // auth|fts|providers|imdb

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
const {
  libraryId: chainLibId, library: chainLibrary, selection: chainSel,
  dirty: chainDirty, draftCount: chainDraftCount, message: chainMsg, orderText: chainOrderText,
  saving: savingChain, testing: testingChain, query: chainQuery,
  testMessage: chainTestMsg, testResult: chainTestResult,
  toggle: toggleChain, move: moveChain, discard: discardChain, save: saveChain, testSearch: testChain,
} = useMatchingSettings(libList, api, async () => { await loadLibs(api); syncLibs() })
function syncLibs() {
  libList.value = listLibs()
  currentId.value = currentMediaId()
}
async function onLibrariesChanged() {
  syncLibs()
  await loadStats()
}
async function onLibrariesLoaded() {
  syncLibs()
  const firstLoad = !ready.value
  ready.value = true
  librariesError.value = ''
  await Promise.all([loadStats(), firstLoad ? activate(route.query) : Promise.resolve()])
}
async function onToolsChanged() {
  await loadStats()
}

const librariesRef = ref(null)

// Panels stay mounted after first use so switching pages retains forms and job progress.
const navs = SETTINGS_PAGES
const navGroups = [...new Set(navs.map(n => n.group))].map(name => ({ name, pages: navs.filter(n => n.group === name) }))
const active = ref(settingsTarget(route.query).page)
const currentPage = computed(() => navs.find(n => n.id === active.value) || navs[0])
const settingsScope = computed(() => {
  if (active.value === 'sec-display') return '作用范围：当前浏览器'
  if (['sec-libtools', 'sec-files', 'sec-matching'].includes(active.value)) return '作用范围：下方选中的视频库'
  if (active.value === 'sec-status') return '统计范围：全部媒体库'
  return '作用范围：此服务的全部媒体库'
})
const visited = ref(new Set([active.value]))
const ready = ref(false)
const librariesError = ref('')
const loadError = ref('')
let navigationGeneration = 0
let disposed = false
async function activate(query) {
  const generation = ++navigationGeneration
  const target = settingsTarget(query)
  active.value = target.page
  visited.value.add(target.page)
  await nextTick()
  if (generation !== navigationGeneration) return
  if (target.page === 'sec-libraries') librariesRef.value?.ensure()
  if ((target.page === 'sec-libtools' || target.page === 'sec-files') && ready.value) {
    await toolsRef.value?.focus(target)
  }
  if (target.page === 'sec-matching' && target.library && libList.value.some(lib => lib.id === target.library)) {
    chainLibId.value = target.library
  }
}
async function go(id) {
  const query = { ...route.query, sec: id }
  for (const key of ['ids', 'library', 'media', 'files_from', 'files_return']) delete query[key]
  const failure = await router.push({ path: '/settings', query })
  if (!failure) window.scrollTo({ top: 0 })
}
async function changeCategory(event) {
  try { await go(event.target.value) }
  finally { event.target.value = active.value }
}
watch(() => route.query, activate)

async function loadSettings() {
  loadError.value = ''
  try {
    s.value = await api('/api/settings')
  } catch (e) { loadError.value = '设置加载失败：' + e.message }
}
async function loadLibraries(force = false) {
  librariesError.value = ''
  try {
    await loadLibs(api, { force })
    if (disposed) return
    syncLibs()
    if (!ready.value) {
      ready.value = true
      await activate(route.query)
    }
  } catch (e) {
    if (!disposed) librariesError.value = e.message
  }
}
onMounted(async () => {
  window.addEventListener('jzmedia:libraries-changed', onLibrariesLoaded)
  await Promise.all([
    loadSettings(),
    loadLibraries(),
    loadStats(), loadProviders(),
  ])
})
onUnmounted(() => {
  disposed = true
  navigationGeneration++
  stopImportImdbTimer()
  window.removeEventListener('jzmedia:libraries-changed', onLibrariesLoaded)
})
</script>
