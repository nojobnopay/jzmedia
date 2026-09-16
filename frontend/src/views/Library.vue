<template>
  <div class="bar">
    <div class="q-wrap">
      <input v-model="q" placeholder="搜片名 / 演员 / 标签" autocomplete="off"
        @input="onQInput" @compositionstart="composing = true" @compositionend="onCompositionEnd"
        @keyup.enter="onSearchEnter" @keydown.down.prevent="suggestMove(1)"
        @keydown.up.prevent="suggestMove(-1)" @keydown.esc.stop="closeSuggest"
        @blur="onQBlur" />
      <ul v-if="suggestOpen" class="suggest">
        <li v-if="suggestNoMatch" class="s-empty">无匹配</li>
        <template v-if="suggestItems.length">
          <li class="s-head">影片</li>
          <li v-for="(s, i) in suggestItems" :key="'m' + s.id"
            :class="{ on: suggestIdx === i }"
            @mousedown.prevent="pickMovie(s)" @mouseenter="suggestIdx = i">
            <span class="s-title">{{ s.title }}</span>
            <span v-if="s.year" class="s-year">({{ s.year }})</span>
          </li>
        </template>
        <template v-if="suggestPersons.length">
          <li class="s-head">演员</li>
          <li v-for="(p, j) in suggestPersons" :key="'p' + p.tmdb_id"
            :class="{ on: suggestIdx === suggestItems.length + j }"
            @mousedown.prevent="pickPerson(p)"
            @mouseenter="suggestIdx = suggestItems.length + j">
            <span class="s-title">{{ p.name }}</span>
            <span class="s-year">库内 {{ p.count }} 部</span>
          </li>
        </template>
      </ul>
    </div>
    <button @click="applyAndLoad">搜索</button>
    <button @click="clearAll">全部</button>
    <button @click="doScan" :disabled="scanning">{{ scanning ? '刮削中…' : '扫描刮削' }}</button>
    <button @click="openUpDlg" :disabled="uploading">上传</button>
  </div>
  <div v-if="msg" class="bar">{{ msg }}</div>

  <div class="filters" v-if="hasFacets">
    <div class="frow">
      <span class="flabel">类型</span>
      <span v-for="g in facets.genres" :key="g.value"
        :class="['chip', { on: sel.genres.includes(g.value) }]"
        @click="toggle('genres', g.value)">{{ g.value }} {{ g.count }}</span>
    </div>
    <div class="frow">
      <span class="flabel">产地</span>
      <span v-for="r in facets.regions" :key="r.value"
        :class="['chip', { on: sel.regions.includes(r.value) }]"
        @click="toggle('regions', r.value)">{{ r.value }} {{ r.count }}</span>
      <span class="fhint">facet内OR、跨维度AND；选了具体国家时大区自动让位</span>
    </div>
    <div class="frow" v-if="facets.countries.length">
      <span class="flabel">国家/地区</span>
      <span v-for="c in facets.countries" :key="c.code || 'unknown'"
        :class="['chip', { on: sel.countries.includes(c.code || '未知') }]"
        @click="toggle('countries', c.code || '未知')">{{ c.name }} {{ c.count }}</span>
    </div>
    <div class="frow">
      <span class="flabel">年代</span>
      <span v-for="d in facets.decades" :key="d.value"
        :class="['chip', { on: sel.decades.includes(String(d.value)) }]"
        @click="toggle('decades', String(d.value))">{{ d.value }}s {{ d.count }}</span>
      <select v-model="yearPick" @change="pickYear">
        <option value="">年份…</option>
        <option v-for="y in facets.years" :key="y.value" :value="y.value">{{ y.value }} ({{ y.count }})</option>
      </select>
      <span v-for="y in sel.years" :key="y" class="chip on" @click="toggle('years', y)">{{ y }} ×</span>
      <span v-if="sel.decades.length" class="fhint">年代与年份叠加为AND（如2020s＋2025＝2025）</span>
    </div>
    <div class="frow" v-if="facets.tags.length">
      <span class="flabel">标签</span>
      <span v-for="t in facets.tags" :key="t.value"
        :class="['chip', 'tag', { on: sel.tags.includes(t.value) }]"
        @click="toggle('tags', t.value)">{{ t.value }} {{ t.count }}</span>
      <span class="fhint">标签多选为AND（逐个收窄）</span>
    </div>
    <div class="frow">
      <span class="flabel">评分</span>
      <select v-model="sel.ratingSource" @change="applyAndLoad">
        <option value="tmdb">TMDB</option>
        <option value="douban">豆瓣</option>
        <option value="custom">自评</option>
      </select>
      <span v-for="s in [9, 8, 7, 6]" :key="s"
        :class="['chip', { on: sel.rating === s, off: ratingCount(s) === 0 }]"
        @click="pickRating(s)">{{ s }}分以上 {{ ratingCount(s) }}</span>
      <span class="fhint">单选；未评分的不计入</span>
    </div>
    <div class="frow">
      <span class="flabel">观看</span>
      <span :class="['chip', { on: sel.watched === 1 }]" @click="pickWatched(1)">已看 {{ watchedCounts.watched }}</span>
      <span :class="['chip', { on: sel.watched === 0 }]" @click="pickWatched(0)">未看 {{ watchedCounts.unwatched }}</span>
    </div>
    <div class="frow" v-if="activeCount">
      <span class="fhint">已选 {{ activeCount }} 项 · 已显示 {{ items.length }} 部<span v-if="hasMore">（还有更多）</span></span>
      <button @click="clearFilters">清空筛选</button>
    </div>
  </div>

  <div class="grid">
    <div v-for="m in items" :key="m.id" :class="['card', { sel: selectedIds.has(m.id) }]" @click="onCard(m)">
      <div class="poster-wrap">
        <button :class="['sel-circle', { on: selectedIds.has(m.id) }]"
          @click.stop="toggleSelect(m.id)" :aria-pressed="selectedIds.has(m.id)" aria-label="选择">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M6.2 11.3 3.1 8.2l-1.4 1.4 4.5 4.5 8.1-8.1-1.4-1.4z" fill="currentColor"/></svg>
        </button>
        <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" loading="lazy" />
        <ScoreBadge :score="m.tmdb_rating" source="tmdb" />
        <span v-if="m.watched" class="watched-badge">✓已看</span>
      </div>
      <div class="t">{{ m.title }} <span v-if="m.year">({{ m.year }})</span><span v-if="m.version_count > 1"> ×{{ m.version_count }}</span><span v-if="m.needs_review"> [待确认]</span><span v-if="hasScore(m.custom_rating)" class="custom-mini">♥{{ fmtScore(m.custom_rating) }}</span><br v-if="m.region || (m.genres || []).length" /><span v-if="m.region" class="meta">{{ m.region }}</span><span v-if="(m.genres || []).length" class="meta"> {{ (m.genres || []).slice(0, 2).join('/') }}</span></div>
    </div>
  </div>
  <div ref="loadSentinel" class="load-more">
    <button v-if="hasMore" @click="loadMore" :disabled="loadingMore">{{ loadingMore ? '加载中…' : '加载更多' }}</button>
    <span v-else-if="items.length" class="fhint">已全部加载（{{ items.length }} 部）</span>
    <span v-if="loadError" class="fhint warn-text">{{ loadError }}</span>
  </div>

  <div v-if="selecting" class="floatbar" role="toolbar" aria-label="多选操作">
    <span class="count">{{ selectedIds.size }}</span>
    <button @click="selectAllVisible" :disabled="!items.length" title="全选当前筛选">
      <svg viewBox="0 0 16 16"><path d="M2 2h12v12H2z" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 8.2 7.2 10.4 11 5.6" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>
      <span>全选</span>
    </button>
    <button @click="batchWatched(true)" :disabled="batching" title="标为已看">
      <svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.8 8 3.8 14.5 8 14.5 8 12 12.2 8 12.2 1.5 8 1.5 8z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="8" r="2.2" fill="currentColor"/></svg>
      <span>已看</span>
    </button>
    <button @click="batchWatched(false)" :disabled="batching" title="标为未看">
      <svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.8 8 3.8c1.5 0 2.9.5 4 1.2M14.5 8S12 12.2 8 12.2c-1.5 0-2.9-.5-4-1.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M3 13 13 3" stroke="currentColor" stroke-width="1.4"/></svg>
      <span>未看</span>
    </button>
    <button @click="openTagDlg" title="批量标签">
      <svg viewBox="0 0 16 16"><path d="M2 2h5.5L14 8.5 8.5 14 2 7.5z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="6" cy="6" r="1.3" fill="currentColor"/></svg>
      <span>标签</span>
    </button>
    <button @click="openColDlg" title="加入合集">
      <svg viewBox="0 0 16 16"><path d="M1.5 4.5c0-.8.7-1.5 1.5-1.5h3l1.2 1.5H13c.8 0 1.5.7 1.5 1.5v5c0 .8-.7 1.5-1.5 1.5H3c-.8 0-1.5-.7-1.5-1.5z" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>
      <span>合集</span>
    </button>
    <button @click="openDelDlg" title="删除选中影片">
      <svg viewBox="0 0 16 16"><path d="M2.5 4h11M6.5 4V2.5h3V4M4 4l.7 9.2c.1.7.6 1.3 1.3 1.3h4c.7 0 1.2-.6 1.3-1.3L12 4" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M6.5 7v4M9.5 7v4" stroke="currentColor" stroke-width="1.4"/></svg>
      <span>删除</span>
    </button>
    <button @click="clearSelection" title="退出多选">
      <svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6"/></svg>
      <span>取消</span>
    </button>
    <span v-if="batchMsg || batching" class="fmsg">{{ batching ? '提交中…' : batchMsg }}</span>
  </div>

  <div v-if="tagDlg" class="dlg-mask" @click.self="tagDlg = false">
    <div class="dlg">
      <h3>批量标签（{{ selectedIds.size }} 部）</h3>
      <div class="bar">
        <label><input type="radio" value="add" v-model="tagMode" /> 追加</label>
        <label><input type="radio" value="remove" v-model="tagMode" /> 移除</label>
      </div>
      <div class="bar">
        <input v-model="newTag" placeholder="新标签，回车加入待办" @keyup.enter="onTagEnter" style="flex:1" />
        <button @click="queueNewTag">加入</button>
      </div>
      <div v-if="pendingTags.length" class="bar">待{{ tagMode === 'add' ? '追加' : '移除' }}：<span v-for="t in pendingTags" :key="t" class="chip on" @click="dropPending(t)">{{ t }} ×</span></div>
      <div class="taglist">
        <span v-for="t in facets.tags" :key="t.value" class="chip tag" @click="queueExisting(t.value)">{{ t.value }} {{ t.count }}</span>
      </div>
      <div class="bar"><button @click="confirmBatchTags" :disabled="batching || !pendingTags.length">{{ batching ? '提交中…' : '确认提交' }}</button><button @click="tagDlg = false">取消</button><span>{{ batchMsg }}</span></div>
    </div>
  </div>

  <div v-if="colDlg" class="dlg-mask" @click.self="colDlg = false">
    <div class="dlg">
      <h3>加入合集（{{ selectedIds.size }} 部）</h3>
      <div class="bar"><input v-model="colQ" placeholder="搜索合集" style="flex:1" /></div>
      <ul class="collist">
        <li v-for="c in filteredCols" :key="c.id"><span>{{ c.name }}（{{ c.member_count }}）</span><button @click="joinCollection(c.id)" :disabled="batching">加入</button></li>
      </ul>
      <div class="bar"><input v-model="newCol" placeholder="新建合集名（含当前选中）" style="flex:1" /><button @click="createAndJoin" :disabled="batching || !newCol.trim()">创建并加入</button></div>
      <div class="bar"><button @click="colDlg = false">关闭</button><span>{{ batchMsg }}</span></div>
    </div>
  </div>

  <div v-if="delDlg" class="dlg-mask" @click.self="delDlg = false">
    <div class="dlg">
      <h3>删除影片（{{ delSummary.total_movies }} 部）</h3>
      <p class="del-warn">警告：将永久删除磁盘文件与库记录（海报/镜像缓存保留），不可恢复。每部片的全部版本与附属文件（花絮/字幕/NFO/周边）都会一起删除。</p>
      <p class="del-sum">{{ delSummary.total_movies }} 部影片 · {{ delSummary.total_versions }} 个正片版本 · {{ delSummary.total_files }} 个文件 · 共 {{ fmtBytes(delSummary.total_bytes) }}</p>
      <ul class="collist">
        <li v-for="p in visibleDelPlans" :key="p.id"><span>{{ p.title || '(未命名)' }}<span v-if="p.year"> ({{ p.year }})</span></span><span class="fhint">{{ p.feature_count }} 版本 · {{ p.extra_count }} 附属 · {{ fmtBytes(p.total_size) }}</span></li>
      </ul>
      <p v-if="delPlans.length > 20" class="fhint">等共 {{ delPlans.length }} 部<span v-if="!showAllDel">（仅列前 20）</span> <button v-if="!showAllDel" @click="showAllDel = true">展开全部</button></p>
      <div class="bar">
        <button v-if="!delDone" @click="confirmDel" :disabled="batching || !delPlans.length" class="danger-btn">{{ batching ? '删除中…' : `确认删除 ${delSummary.total_movies} 部影片（${delSummary.total_files} 个文件）` }}</button>
        <button @click="delDlg = false">{{ delDone ? '关闭' : '取消' }}</button>
        <span>{{ batchMsg }}</span>
      </div>
    </div>
  </div>

  <div v-if="upDlg" class="dlg-mask" @click.self="closeUpDlg">
    <div class="dlg">
      <h3>{{ upStep === 'organize' ? '归档整理（第 2 步）' : upStep === 'done' ? '完成' : '上传到媒体库（第 1 步）' }}</h3>
      <template v-if="upStep === 'upload'">
      <div class="bar">
        <label><input type="radio" value="files" v-model="upMode" :disabled="uploading" @change="onUpModeChange" /> 多选文件</label>
        <label><input type="radio" value="dir" v-model="upMode" :disabled="uploading" @change="onUpModeChange" /> 整个文件夹</label>
      </div>
      <div class="bar">
        <input v-if="upMode === 'files'" type="file" multiple ref="upFiles" :disabled="uploading" @change="onUpInputChange" />
        <input v-else type="file" webkitdirectory ref="upDir" :disabled="uploading" @change="onUpInputChange" />
      </div>
      <div class="bar"><span class="fhint">上传到 待整理/，文件夹结构原样保留；字幕/花絮自动归属；同名文件跳过不覆盖；&gt;2GB 建议局域网操作，可随时取消</span></div>
      <div v-if="upFolderHead" class="bar"><span>已选文件夹：{{ upFolderHead }}</span></div>
      <div v-if="upQueue.length" class="bar"><span class="fhint">共 {{ upQueue.length }} 个文件 · {{ fmtBytes(upTotalSize) }}{{ upDoneCount ? ` · 已传 ${upDoneCount}` : '' }}{{ upScanning ? ' · 当前已传完 · 刮削中…' : (upCurPct != null ? ` · 当前 ${upCurPct}%` : '') }}</span></div>
      <div v-if="upQueue.length" class="up-progress"><div class="up-progress-fill" :style="{ width: upTotalPct + '%' }"></div></div>
      <div v-if="upScanning" class="bar"><span class="up-scan">{{ upScanHint }}</span></div>
      <ul v-if="upQueue.length" class="collist">
        <li v-for="(t, i) in visibleUpQueue" :key="i"><span :title="t.rel">{{ midEllipsis(t.rel) }}</span><span class="fhint">{{ upTaskState(t) }}</span></li>
      </ul>
      <p v-if="upQueue.length > 50" class="fhint">等共 {{ upQueue.length }} 个<span v-if="!showAllUp">（仅列前 50）</span> <button v-if="!showAllUp" @click="showAllUp = true">展开全部</button></p>
      <div v-if="upSummary" class="bar"><span class="fhint">{{ upSummary }}</span></div>
      <div v-if="upNeedsMatch.length" class="bar"><span class="fhint">以下需确认匹配：</span></div>
      <ul v-if="upNeedsMatch.length" class="collist">
        <li v-for="t in upNeedsMatch" :key="t.rel"><span :title="t.rel">{{ midEllipsis(t.rel) }}（{{ t.note }}）</span><button v-if="t.movieId" @click="router.push('/m/' + t.movieId)">去详情匹配</button></li>
      </ul>
      <div class="bar">
        <button v-if="!uploading && !upFinished" @click="startUpload" :disabled="!canStartUpload">开始上传</button>
        <button v-if="uploading" @click="cancelUpload">取消上传</button>
        <button v-if="upFinished && upOrganizable" @click="goUpOrganize">下一步：归档整理</button>
        <button v-if="!uploading" @click="closeUpDlg">{{ upFinished ? '关闭' : '取消' }}</button>
        <span>{{ upMsg }}</span>
      </div>
      </template>
      <template v-if="upStep === 'organize'">
      <div class="bar"><span class="fhint">待整理 → 电影（按大区），仅本次上传的 {{ upOrganizableIds.length }} 部影片，先预览再执行</span></div>
      <div class="bar"><span>{{ upOrgMsg }}</span></div>
      <ul v-if="upOrgPlans.length" class="collist">
        <li v-for="p in upOrgPlans" :key="p.id"><span :title="p.from + ' → ' + p.to">{{ midEllipsis(p.from, 40) }} → {{ midEllipsis(p.to, 40) }}</span><span v-if="p.status" class="fhint">{{ p.status }}</span></li>
      </ul>
      <div v-if="upOrgConflicts.length" class="bar"><span class="fhint">冲突 {{ upOrgConflicts.length }} 项，需先去详情匹配：</span></div>
      <ul v-if="upOrgConflicts.length" class="collist">
        <li v-for="c in upOrgConflicts" :key="'c' + c.id"><span :title="(c.title || '') + ' ' + c.from">{{ midEllipsis(c.title || c.from) }}（{{ c.status }}）</span><button @click="router.push('/m/' + c.id)">去详情匹配</button></li>
      </ul>
      <div class="bar">
        <button v-if="!upOrgDone" @click="doUpOrganize" :disabled="upOrgBusy || !upOrgPlans.length">{{ upOrgBusy ? '执行中…' : '确认搬迁' }}</button>
        <button @click="closeUpDlg" :disabled="upOrgBusy">{{ upOrgDone ? '完成' : '稍后整理' }}</button>
        <span>{{ upOrgMsg }}</span>
      </div>
      </template>
      <template v-if="upStep === 'done'">
      <div class="bar"><span class="fhint">{{ upDoneSummary }}</span></div>
      <div class="bar"><button @click="closeUpDlg">关闭</button></div>
      </template>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiUpload, posterUrl } from '../api.js'
import ScoreBadge from '../components/ScoreBadge.vue'
import { hasScore, fmtScore } from '../ratings.js'

const route = useRoute()
const router = useRouter()

const q = ref('')
const suggestOpen = ref(false)
const suggestItems = ref([])
const suggestPersons = ref([])
const suggestNoMatch = ref(false)
const suggestIdx = ref(-1)
let suggestTimer = null
let suggestSeq = 0
let composing = false
const loadSentinel = ref(null)
let loadIO = null
const items = ref([])
const msg = ref('')
const scanning = ref(false)
const PAGE = 60                 // 每页条数（评审 P1-11：>500 部不再被后端默认截断）
const hasMore = ref(false)
const loadingMore = ref(false)
const loadError = ref('')
let loading = false
let loadSeq = 0
const facets = ref({ genres: [], regions: [], countries: [], years: [], decades: [], tags: [], collections: [], watched: { watched: 0, unwatched: 0 }, ratings: { tmdb: [], douban: [], custom: [] } })
const sel = ref({ genres: [], regions: [], countries: [], years: [], decades: [], tags: [], watched: null, rating: null, ratingSource: 'tmdb' })
const yearPick = ref('')

const watchedCounts = computed(() => facets.value.watched || { watched: 0, unwatched: 0 })

// 多选态（海报粒度：selectedIds 存代表行 id，服务端展开到同 tmdb 全版本）
// Plex 式：首勾自动进入，清空/Esc 自动退出，无手动开关
const selectedIds = ref(new Set())
const selecting = computed(() => selectedIds.value.size > 0)
const batching = ref(false)
const batchMsg = ref('')
const tagDlg = ref(false)
const tagMode = ref('add')
const newTag = ref('')
const pendingTags = ref([])
const colDlg = ref(false)
const colQ = ref('')
const newCol = ref('')
const colItems = ref([])
const filteredCols = computed(() => {
  const q = colQ.value.trim()
  const src = colItems.value.length ? colItems.value : (facets.value.collections || [])
  if (!q) return src
  return src.filter(c => (c.name || '').includes(q))
})

function ratingCount(s) {
  const arr = (facets.value.ratings || {})[sel.value.ratingSource] || []
  const hit = arr.find(x => x.min === s)
  return hit ? hit.count : 0
}
function pickRating(s) {
  sel.value.rating = (sel.value.rating === s) ? null : s
  applyAndLoad()
}

const hasFacets = computed(() =>
  facets.value.genres.length || facets.value.regions.length || facets.value.years.length)
const activeCount = computed(() =>
  sel.value.genres.length + sel.value.regions.length + sel.value.countries.length +
  sel.value.years.length + sel.value.decades.length + sel.value.tags.length +
  (sel.value.watched == null ? 0 : 1) +
  (sel.value.rating == null ? 0 : 1))

function pickWatched(v) {
  sel.value.watched = (sel.value.watched === v) ? null : v
  applyAndLoad()
}

function toggle(key, v) {
  const a = sel.value[key]
  const i = a.indexOf(v)
  if (i >= 0) a.splice(i, 1)
  else a.push(v)
  applyAndLoad()
}
function pickYear() {
  if (yearPick.value && !sel.value.years.includes(String(yearPick.value))) {
    sel.value.years.push(String(yearPick.value))
  }
  yearPick.value = ''
  applyAndLoad()
}
function _qNorm(query) {
  const parts = []
  for (const k of Object.keys(query || {}).sort()) {
    const v = query[k]
    parts.push(k + '=' + (Array.isArray(v) ? v.join(',') : String(v ?? '')))
  }
  return parts.join('&')
}
function syncUrl() {
  const query = {}
  if (q.value.trim()) query.q = q.value.trim()
  if (sel.value.genres.length) query.genre = sel.value.genres.join(',')
  if (sel.value.regions.length) query.region = sel.value.regions.join(',')
  if (sel.value.countries.length) query.country = sel.value.countries.join(',')
  if (sel.value.years.length) query.year = sel.value.years.join(',')
  if (sel.value.decades.length) query.decade = sel.value.decades.join(',')
  if (sel.value.tags.length) query.tag = sel.value.tags.join(',')
  if (sel.value.watched != null) query.watched = String(sel.value.watched)
  if (sel.value.rating != null) {
    query.min_rating = String(sel.value.rating)
    if (sel.value.ratingSource !== 'tmdb') query.rating_source = sel.value.ratingSource
  }
  const changed = _qNorm(query) !== _qNorm(route.query)
  if (changed) router.replace({ path: '/', query })
  return changed   // 变则交给 route.query watcher 加载；未变由调用方显式刷新
}
function readUrl() {
  const s = (v) => v ? String(v).split(',').map(x => x.trim()).filter(Boolean) : []
  const src = String(route.query.rating_source || 'tmdb')
  const wq = Array.isArray(route.query.watched) ? route.query.watched[0] : route.query.watched
  q.value = route.query.q || ''
  sel.value = {
    genres: s(route.query.genre),
    regions: s(route.query.region),
    countries: s(route.query.country),
    years: s(route.query.year),
    decades: s(route.query.decade),
    tags: s(route.query.tag),
    watched: wq != null && wq !== '' ? Number(wq) : null,
    rating: route.query.min_rating != null && route.query.min_rating !== '' ? Number(route.query.min_rating) : null,
    ratingSource: ['tmdb', 'douban', 'custom'].includes(src) ? src : 'tmdb',
  }
}
function buildParams(offset = 0) {
  const p = new URLSearchParams()
  if (q.value.trim()) p.set('q', q.value.trim())
  // 选了具体国家时大区自动让位（后端两者是AND，避免华语+US这种空交集）
  const useRegion = sel.value.countries.length ? [] : sel.value.regions
  for (const [key, vals] of [['genre', sel.value.genres], ['region', useRegion],
      ['country', sel.value.countries], ['year', sel.value.years],
      ['decade', sel.value.decades], ['tag', sel.value.tags]]) {
    for (const v of vals) p.append(key, v)
  }
  if (sel.value.watched != null) p.set('watched', String(sel.value.watched))
  if (sel.value.rating != null) {
    p.set('min_rating', String(sel.value.rating))
    p.set('rating_source', sel.value.ratingSource)
  }
  p.set('limit', String(PAGE))   // 分页（评审 P1-11）：加载更多而非一次全量
  p.set('offset', String(Math.max(0, offset)))
  return p.toString()
}
async function load() {
  const seq = ++loadSeq
  loading = true
  try {
    const d = await api('/api/search?' + buildParams(0))
    if (seq !== loadSeq) return          // 更新的筛选已接管，丢弃过期回包
    items.value = d.items || []
    hasMore.value = !!d.has_more
    loadError.value = ''
  } catch (e) {
    if (seq === loadSeq) loadError.value = '加载失败：' + e.message
  } finally {
    if (seq === loadSeq) loading = false
  }
}
async function loadMore() {
  if (!hasMore.value || loading) return
  const seq = ++loadSeq
  loading = true
  loadingMore.value = true
  try {
    const d = await api('/api/search?' + buildParams(items.value.length))
    if (seq !== loadSeq) return
    const known = new Set(items.value.map(m => m.id))
    for (const m of (d.items || [])) {
      if (!known.has(m.id)) items.value.push(m)
    }
    hasMore.value = !!d.has_more
    loadError.value = ''
  } catch (e) {
    if (seq === loadSeq) loadError.value = '加载更多失败：' + e.message
  } finally {
    if (seq === loadSeq) { loading = false; loadingMore.value = false }
  }
}
async function applyAndLoad() {
  // URL 变了 → route.query watcher 统一加载；没变（如回车搜索词未改）才显式刷新。
  // 修复评审 B5a-9/R04-D3：此前这里与 watcher 各发一次完全相同的 /api/search
  if (!syncUrl()) await load()
}
// 搜索联想：输入防抖拉本地库（影片 标题+年份 / 演员 名字+参演数），↑↓选择 / Enter选中 / Esc关闭
function onQInput(e) {
  if (composing || (e && e.isComposing)) return
  scheduleSuggest()
}
function onCompositionEnd() {
  composing = false
  scheduleSuggest()
}
function scheduleSuggest() {
  clearTimeout(suggestTimer)
  const term = q.value.trim()
  if (!term) {
    closeSuggest()
    return
  }
  suggestTimer = setTimeout(fetchSuggest, 180)
}
async function fetchSuggest() {
  const term = q.value.trim()
  if (!term) return
  const seq = ++suggestSeq
  try {
    const d = await api('/api/search/suggest?q=' + encodeURIComponent(term) + '&limit=8')
    if (seq !== suggestSeq) return
    suggestItems.value = (d && d.items) || []
    suggestPersons.value = (d && d.persons) || []
    suggestNoMatch.value = !suggestItems.value.length && !suggestPersons.value.length
    suggestIdx.value = -1
    suggestOpen.value = true
  } catch (e) {
    if (seq === suggestSeq) closeSuggest()
  }
}
function suggestRow(idx) {
  if (idx < 0) return null
  if (idx < suggestItems.value.length) return { kind: 'movie', v: suggestItems.value[idx] }
  const j = idx - suggestItems.value.length
  if (j < suggestPersons.value.length) return { kind: 'person', v: suggestPersons.value[j] }
  return null
}
function suggestMove(d) {
  if (!suggestOpen.value) return
  const n = suggestItems.value.length + suggestPersons.value.length
  if (!n) return
  let i = suggestIdx.value + d
  if (i < 0) i = n - 1
  if (i >= n) i = 0
  suggestIdx.value = i
}
function pickRow(row) {
  clearTimeout(suggestTimer)
  suggestSeq++
  q.value = row.kind === 'movie' ? row.v.title : row.v.name
  closeSuggest()
  applyAndLoad()
}
// 演员联想：选中即按演员名搜索其参演影片（与手敲回车一致）
function pickMovie(s) { pickRow({ kind: 'movie', v: s }) }
function pickPerson(p) { pickRow({ kind: 'person', v: p }) }
function closeSuggest() {
  suggestOpen.value = false
  suggestItems.value = []
  suggestPersons.value = []
  suggestNoMatch.value = false
  suggestIdx.value = -1
}
function onQBlur() {
  setTimeout(closeSuggest, 120)
}
function onSearchEnter(e) {
  if (e && (e.isComposing || e.keyCode === 229)) return
  clearTimeout(suggestTimer)
  suggestSeq++
  const row = suggestOpen.value ? suggestRow(suggestIdx.value) : null
  if (row) {
    pickRow(row)
    return
  }
  closeSuggest()
  applyAndLoad()
}
async function showAll() {
  q.value = ''
  sel.value = { genres: [], regions: [], countries: [], years: [], decades: [], tags: [], watched: null, rating: null, ratingSource: 'tmdb' }
  if (!syncUrl()) await load()
}
async function clearFilters() {
  sel.value = { genres: [], regions: [], countries: [], years: [], decades: [], tags: [], watched: null, rating: null, ratingSource: 'tmdb' }
  await applyAndLoad()
}
async function clearAll() { await showAll() }
async function loadFacets() {
  try { facets.value = await api('/api/facets') } catch (e) { /* 库空时忽略 */ }
  try { colItems.value = (await api('/api/collections')).items || [] } catch (e) { /* 忽略 */ }
}
function onCard(m) {
  if (selecting.value) toggleSelect(m.id)
  else router.push('/m/' + m.id)
}
function toggleSelect(id) {
  const s = new Set(selectedIds.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  selectedIds.value = s
}
function selectAllVisible() {
  const s = new Set(selectedIds.value)
  for (const m of items.value) s.add(m.id)
  selectedIds.value = s
}
function clearSelection() {
  selectedIds.value = new Set()
}
async function batchCall(ops) {
  batching.value = true
  batchMsg.value = ''
  try {
    const d = await api('/api/movies/batch', {
      method: 'POST',
      body: JSON.stringify({ ids: [...selectedIds.value], ops })
    })
    batchMsg.value = `完成：${d.affected_versions} 个文件版本`
    clearSelection()
    tagDlg.value = false
    pendingTags.value = []
    await loadFacets()
    await load()
  } catch (e) {
    batchMsg.value = '批量失败：' + e.message
  } finally {
    batching.value = false
  }
}
async function batchWatched(w) {
  await batchCall({ watched: w })
}
function openTagDlg() {
  pendingTags.value = []
  newTag.value = ''
  tagMode.value = 'add'
  batchMsg.value = ''
  tagDlg.value = true
}
function queueNewTag() {
  const t = newTag.value.trim().slice(0, 20)
  if (t && !pendingTags.value.includes(t)) pendingTags.value.push(t)
  newTag.value = ''
}
function onTagEnter(e) {
  if (e && (e.isComposing || e.keyCode === 229)) return
  queueNewTag()
}
function queueExisting(t) {
  if (!pendingTags.value.includes(t)) pendingTags.value.push(t)
}
function dropPending(t) {
  pendingTags.value = pendingTags.value.filter(x => x !== t)
}
async function confirmBatchTags() {
  if (!pendingTags.value.length) return
  const ops = tagMode.value === 'add'
    ? { add_tags: pendingTags.value }
    : { remove_tags: pendingTags.value }
  await batchCall(ops)
}
function openColDlg() {
  colQ.value = ''
  newCol.value = ''
  batchMsg.value = ''
  colDlg.value = true
}
// 整片删除：先预览影响（片名+版本/附属/大小），二次确认后执行
const delDlg = ref(false)
const delPlans = ref([])
const delSummary = ref({ total_movies: 0, total_versions: 0, total_files: 0, total_bytes: 0 })
const delDone = ref(false)
const showAllDel = ref(false)
const visibleDelPlans = computed(() => showAllDel.value ? delPlans.value : delPlans.value.slice(0, 20))
function fmtBytes(n) {
  n = Number(n) || 0
  if (n < 1024) return n + 'B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'M'
  return (n / 1024 / 1024 / 1024).toFixed(2) + 'G'
}
async function openDelDlg() {
  batchMsg.value = ''
  delDone.value = false
  showAllDel.value = false
  batching.value = true
  try {
    const d = await api('/api/movies/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: [...selectedIds.value], dry_run: true })
    })
    delPlans.value = d.plans || []
    delSummary.value = {
      total_movies: d.total_movies || 0, total_versions: d.total_versions || 0,
      total_files: d.total_files || 0, total_bytes: d.total_bytes || 0
    }
    delDlg.value = true
  } catch (e) {
    batchMsg.value = '删除预览失败：' + e.message
  } finally {
    batching.value = false
  }
}
async function confirmDel() {
  batching.value = true
  batchMsg.value = ''
  try {
    const d = await api('/api/movies/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: [...selectedIds.value], dry_run: false, confirm: true })
    })
    delDone.value = true
    batchMsg.value = `已删除 ${d.deleted_movies}/${d.total_movies} 部（${d.total_files} 个文件）`
    clearSelection()
    await loadFacets()
    await load()
  } catch (e) {
    batchMsg.value = '删除失败：' + e.message
  } finally {
    batching.value = false
  }
}async function joinCollection(cid) {
  batching.value = true
  batchMsg.value = ''
  try {
    await api(`/api/collections/${cid}/members`, {
      method: 'POST',
      body: JSON.stringify({ movie_ids: [...selectedIds.value] })
    })
    batchMsg.value = '已加入合集'
    clearSelection()
    await loadFacets()
    try { colItems.value = (await api('/api/collections')).items || [] } catch (e) { /* 忽略 */ }
  } catch (e) {
    batchMsg.value = '加入失败：' + e.message
  } finally {
    batching.value = false
  }
}
async function createAndJoin() {
  const name = newCol.value.trim()
  if (!name) return
  batching.value = true
  batchMsg.value = ''
  try {
    const d = await api('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name, member_ids: [...selectedIds.value] })
    })
    batchMsg.value = `已建合集「${d.name}」`
    newCol.value = ''
    clearSelection()
    await loadFacets()
    try { colItems.value = (await api('/api/collections')).items || [] } catch (e) { /* 忽略 */ }
  } catch (e) {
    batchMsg.value = '创建失败：' + e.message
  } finally {
    batching.value = false
  }
}
async function doScan() {
  scanning.value = true
  msg.value = ''
  try {
    const d = await api('/api/scan', { method: 'POST' })
    const ok = d.results.filter(r => r.status === 'ok').length
    msg.value = `完成：${ok}/${d.results.length} 匹配成功`
    await loadFacets()
    await load()
  } catch (e) {
    msg.value = '扫描失败：' + e.message
  } finally {
    scanning.value = false
  }
}

// 库页上传：多选文件 / 整个文件夹 → POST /api/uploads 逐个顺序上传。
// 文件夹模式透传 webkitRelativePath，后端原样还原结构；落盘即调 scan_one，
// 所以完成后只需刷新海报墙，未匹配的走设置页现有流程。
const upDlg = ref(false)
const upMode = ref('files')
const upFiles = ref(null)
const upDir = ref(null)
const upFolderHead = ref('')
const upQueue = ref([])
const uploading = ref(false)
const upMsg = ref('')
const upSummary = ref('')
const showAllUp = ref(false)
const upCurPct = ref(null)
let upAbort = null
let upCancelled = false
const upTotalSize = computed(() => upQueue.value.reduce((a, t) => a + (t.file.size || 0), 0))
const upDoneCount = computed(() => upQueue.value.filter(t => t.state === 'done' || t.state === 'skipped' || t.state === 'error').length)
const upTotalPct = computed(() => {
  const n = upQueue.value.length
  if (!n) return 0
  return Math.min(100, Math.round((upDoneCount.value * 100 + (upCurPct.value || 0)) / n))
})
// 长路径中间缩写：保留头部 + ... + 尾部文件名，hover 用 title 看全名
function midEllipsis(s, max = 48) {
  s = String(s || '')
  if (s.length <= max) return s
  const tail = 17, head = Math.max(1, max - tail - 3)
  return s.slice(0, head) + '...' + s.slice(-tail)
}
const upFinished = computed(() => upQueue.value.length > 0 && !uploading.value && upQueue.value.every(t => t.state !== 'queued' && t.state !== 'active'))
const canStartUpload = computed(() => !uploading.value && upQueue.value.some(t => t.state === 'queued'))
const visibleUpQueue = computed(() => showAllUp.value ? upQueue.value : upQueue.value.slice(0, 50))
function upTaskState(t) {
  if (t.state === 'queued') return fmtBytes(t.file.size)
  if (t.state === 'active') {
    if (t.phase === 'scanning') return '已传完 · 联网刮削中…'
    return (upCurPct.value != null ? upCurPct.value + '% · ' : '') + '上传中…'
  }
  if (t.state === 'skipped') return '已存在·跳过'
  if (t.state === 'error') return '失败：' + (t.note || '')
  return t.note || '完成'
}
// 字节传完后的等待提示（服务端在响应前同步跑 scan_one：TMDB 搜索/详情/海报头像）
const upScanSecs = ref(0)
const scanSamples = ref([])
let upScanTimer = null
function startScanTicker() {
  if (upScanTimer) clearInterval(upScanTimer)
  upScanSecs.value = 0
  upScanTimer = setInterval(() => { upScanSecs.value++ }, 1000)
}
function stopScanTicker() {
  if (upScanTimer) { clearInterval(upScanTimer); upScanTimer = null }
}
const upScanning = computed(() => upQueue.value.some(t => t.state === 'active' && t.phase === 'scanning'))
const upScanHint = computed(() => {
  const base = `已传完，正在联网匹配 TMDB 元数据并下载海报，预计约 10–30 秒，请耐心等待（已等待 ${upScanSecs.value}s）`
  const samples = scanSamples.value
  if (!samples.length) return base
  const avg = Math.max(1, Math.round(samples.reduce((a, b) => a + b, 0) / samples.length / 1000))
  const left = upQueue.value.filter(t => t.state === 'queued' || (t.state === 'active' && t.phase === 'scanning')).length
  return `${base}；本会话平均约 ${avg} 秒/部，共 ${left} 部待刮削，预计还需约 ${avg * left} 秒`
})
function stageName(f) {
  const rel = (upMode.value === 'dir' && f.webkitRelativePath) ? f.webkitRelativePath : f.name
  return String(rel || f.name || '').replace(/\\/g, '/')
}
function openUpDlg() {
  upDlg.value = true
  upStep.value = 'upload'
  upMsg.value = ''
  upSummary.value = ''
  upQueue.value = []
  upFolderHead.value = ''
  showAllUp.value = false
  upCurPct.value = null
  scanSamples.value = []
  stopScanTicker()
  upOrgPlans.value = []
  upOrgConflicts.value = []
  upOrgMsg.value = ''
  upOrgDone.value = false
  upDoneSummary.value = ''
}
function closeUpDlg() {
  if (uploading.value || upOrgBusy.value) return
  upDlg.value = false
}
function collectStaged() {
  const input = upMode.value === 'dir' ? upDir.value : upFiles.value
  const files = (input && input.files) ? Array.from(input.files) : []
  const out = []
  for (const f of files) {
    const rel = stageName(f)
    // 跳过隐藏文件（.DS_Store 等）与空路径
    if (!rel || rel.split('/').some(s => s.startsWith('.'))) continue
    out.push({ file: f, rel, state: 'queued', phase: 'uploading', note: '', movieId: null })
  }
  return out
}
function syncFolderHead() {
  upFolderHead.value = ''
  if (upMode.value !== 'dir' || !upQueue.value.length) return
  const top = (upQueue.value[0].rel.split('/')[0] || '').trim()
  if (!top) return
  upFolderHead.value = `${top}（${upQueue.value.length} 个文件 · ${fmtBytes(upTotalSize.value)}）`
}
function onUpInputChange() {
  if (uploading.value) return
  upQueue.value = collectStaged()
  showAllUp.value = false
  upSummary.value = ''
  syncFolderHead()
  if (!upQueue.value.length) {
    upMsg.value = upMode.value === 'dir' ? '所选文件夹没有可上传的文件' : '先选择文件'
  } else {
    upMsg.value = ''
  }
}
function onUpModeChange() {
  if (uploading.value) return
  upQueue.value = []
  upFolderHead.value = ''
  upSummary.value = ''
  upMsg.value = ''
  showAllUp.value = false
}
async function startUpload() {
  const staged = upQueue.value.length ? upQueue.value : collectStaged()
  if (!staged.length) {
    upMsg.value = upMode.value === 'dir' ? '先选择文件夹' : '先选择文件'
    return
  }
  if (staged.length > 500) {
    upMsg.value = `一次最多 500 个文件（当前 ${staged.length} 个），请分批上传`
    return
  }
  upQueue.value = staged
  syncFolderHead()
  upMsg.value = ''
  upSummary.value = ''
  uploading.value = true
  upCancelled = false
  scanSamples.value = []
  stopScanTicker()
  let ok = 0, skipped = 0, failed = 0, review = 0, nomatch = 0
  for (const t of upQueue.value) {
    if (upCancelled) break
    if (t.state !== 'queued') continue
    t.state = 'active'
    t.phase = 'uploading'
    t.scanStartedAt = 0
    upCurPct.value = 0
    const h = apiUpload('/api/uploads', t.file, {
      fields: { relpath: t.rel },
      onProgress: (p) => { upCurPct.value = p },
      onUploaded: () => {
        // 延迟 800ms 再切“刮削中”，字幕/花絮等本地快路径不会闪提示
        if (t._hintTimer) clearTimeout(t._hintTimer)
        t._hintTimer = setTimeout(() => {
          if (t.state === 'active' && t.phase === 'uploading') {
            t.phase = 'scanning'
            t.scanStartedAt = Date.now()
            startScanTicker()
          }
        }, 800)
      }
    })
    upAbort = h.abort
    try {
      const r = await h.promise
      clearTimeout(t._hintTimer)
      t._hintTimer = null
      if (t.scanStartedAt) {
        scanSamples.value.push(Date.now() - t.scanStartedAt)
        t.scanStartedAt = 0
      }
      stopScanTicker()
      t.phase = 'uploading'
      const st = (r && r.status) || 'stored'
      t.state = 'done'
      t.note = st
      t.movieId = (r && r.movie_id) || null
      ok++
      if (st === 'ok_needs_review' || (st || '').startsWith('stored_scan_warn')) review++
      else if (st === 'no_match') nomatch++
    } catch (e) {
      clearTimeout(t._hintTimer)
      t._hintTimer = null
      t.scanStartedAt = 0
      stopScanTicker()
      t.phase = 'uploading'
      const m = String((e && e.message) || e)
      if (m === '已取消' || upCancelled) {
        t.state = 'queued'
        t.note = ''
      } else if (/^409\b/.test(m)) {
        t.state = 'skipped'
        skipped++
      } else {
        t.state = 'error'
        t.note = m.slice(0, 120)
        failed++
      }
    } finally {
      upAbort = null
    }
  }
  uploading.value = false
  upCurPct.value = null
  stopScanTicker()
  const parts = [`上传完成：成功 ${ok}`]
  if (skipped) parts.push(`跳过 ${skipped}`)
  if (nomatch) parts.push(`未匹配 ${nomatch}`)
  if (review) parts.push(`待确认 ${review}`)
  if (failed) parts.push(`失败 ${failed}`)
  if (upCancelled) parts.push('（已取消）')
  upSummary.value = parts.join(' · ')
  await loadFacets()
  await load()
}
function cancelUpload() {
  upCancelled = true
  if (upAbort) upAbort()
}
// 归档整理（第 2 步）：仅本次上传影片，待整理 → 电影（按大区），先预览再执行
const upStep = ref('upload')
const upOrgPlans = ref([])
const upOrgConflicts = ref([])
const upOrgMsg = ref('')
const upOrgBusy = ref(false)
const upOrgDone = ref(false)
const upDoneSummary = ref('')
const upNeedsMatch = computed(() => upQueue.value.filter(t => {
  if (t.state !== 'done') return false
  const s = t.note || ''
  return s === 'no_match' || s === 'ok_needs_review' || s.startsWith('stored_scan_warn')
}))
const upOrganizableIds = computed(() => [...new Set(
  upQueue.value.filter(t => t.movieId).map(t => t.movieId))])
const upOrganizable = computed(() => upOrganizableIds.value.length > 0)
function upOrgBody(dry_run) {
  return JSON.stringify({ mode: 'relocate', from_prefix: '待整理', to_dir: '电影',
    group_by_region: true, ids: upOrganizableIds.value, dry_run })
}
async function goUpOrganize() {
  upStep.value = 'organize'
  upOrgPlans.value = []
  upOrgConflicts.value = []
  upOrgDone.value = false
  upOrgMsg.value = '预览中…'
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: upOrgBody(true) })
    upOrgPlans.value = d.plans || []
    upOrgConflicts.value = d.conflicts || []
    upOrgMsg.value = upOrgPlans.value.length ? `可搬迁 ${upOrgPlans.value.length} 项`
      : (upOrgConflicts.value.length ? `无可搬迁，冲突 ${upOrgConflicts.value.length} 项` : '没有需要整理的')
  } catch (e) {
    upOrgMsg.value = '预览失败：' + e.message
  }
}
async function doUpOrganize() {
  upOrgBusy.value = true
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: upOrgBody(false) })
    upOrgPlans.value = d.results || []
    upOrgConflicts.value = d.conflicts || []
    const ok = upOrgPlans.value.filter(r => r.status === 'moved').length
    upOrgDone.value = true
    upDoneSummary.value = `搬迁完成：${ok}/${upOrgPlans.value.length}，已归档到正式库`
    upStep.value = 'done'
    await loadFacets()
    await load()
  } catch (e) {
    upOrgMsg.value = '执行失败：' + e.message
  } finally {
    upOrgBusy.value = false
  }
}
onMounted(async () => {
  readUrl()
  await loadFacets()
  await load()
  window.addEventListener('keydown', escExit)
  // 无限滚动（评审 P1-11）：哨兵进入视口前 600px 自动加载下一页；按钮仍保留作兜底
  try {
    if (window.IntersectionObserver && loadSentinel.value) {
      loadIO = new IntersectionObserver((entries) => {
        if (entries.some(e => e.isIntersecting)) loadMore()
      }, { rootMargin: '600px 0px' })
      loadIO.observe(loadSentinel.value)
    }
  } catch (e) { /* 不支持则只用按钮 */ }
})
onUnmounted(() => {
  window.removeEventListener('keydown', escExit)
  stopScanTicker()
  clearTimeout(suggestTimer)
  if (loadIO) { try { loadIO.disconnect() } catch (e) { /* 忽略 */ } loadIO = null }
})
function escExit(e) {
  if (e.key === 'Escape' && selecting.value && !suggestOpen.value && !tagDlg.value && !colDlg.value && !delDlg.value && !upDlg.value) {
    clearSelection()
  }
}
watch(() => route.query, () => { readUrl(); load() })
</script>
<style scoped>
.filters { padding: 0 12px; display: flex; flex-direction: column; gap: 6px; }
.frow { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.flabel { color: #888; font-size: 0.8125rem; min-width: 56px; }
.chip { font-size: 0.8125rem; padding: 4px 10px; border: 1px solid #444; border-radius: 999px; cursor: pointer; background: #1c1c1c; }
.chip.on { border-color: #e50914; color: #ff8a8a; }
.chip.tag { border-style: dashed; }
.chip.off { opacity: .45; }
.fhint { color: #777; font-size: 0.75rem; }
.q-wrap { position: relative; flex: 0 1 260px; }
.q-wrap input { width: 100%; box-sizing: border-box; }
.suggest {
  position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 60;
  list-style: none; margin: 0; padding: 4px 0; max-height: 320px; overflow: auto;
  background: #1c1c1c; border: 1px solid #444; border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,.55);
}
.suggest li { padding: 6px 12px; cursor: pointer; display: flex; gap: 6px; align-items: baseline; }
.suggest li.on { background: #333; }
.suggest .s-head { color: #777; font-size: 0.75rem; padding: 6px 12px 2px; cursor: default; }
.suggest .s-title { color: #eee; }
.suggest .s-year { color: #888; font-size: 0.8125rem; }
.suggest .s-empty { color: #777; cursor: default; }
.up-scan { color: #e0a63c; font-size: 0.8125rem; }
.meta { color: #888; font-size: 0.75rem; }
.custom-mini { color: #ff6b6b; font-size: 0.75rem; }
.selbar { display: none; }
.card.sel { outline: 2px solid #e50914; }
.card.sel img { filter: brightness(.75); }
.sel-circle {
  position: absolute; top: 6px; left: 6px; z-index: 2;
  width: 26px; height: 26px; padding: 0; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  border: 2px solid rgba(255,255,255,.85); background: rgba(0,0,0,.55); color: transparent;
  opacity: 0; transition: opacity .15s; cursor: pointer;
}
.poster-wrap:hover .sel-circle, .sel-circle.on { opacity: 1; }
.sel-circle.on { background: #e50914; border-color: #e50914; color: #fff; }
@media (hover: none) { .sel-circle { opacity: 1; } }
.floatbar {
  position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%); z-index: 40;
  display: flex; gap: 4px; align-items: center;
  background: rgba(28,28,28,.96); border: 1px solid #e50914; border-radius: 999px;
  padding: 8px 14px; box-shadow: 0 8px 28px rgba(0,0,0,.6);
  max-width: calc(100vw - 24px); overflow-x: auto;
}
.floatbar .count {
  min-width: 26px; height: 26px; border-radius: 50%;
  background: #e50914; color: #fff; font-weight: bold; font-size: 0.8125rem;
  display: flex; align-items: center; justify-content: center; padding: 0 6px;
}
.floatbar button {
  display: flex; gap: 5px; align-items: center; border: none; background: transparent;
  color: #eee; white-space: nowrap; font-size: 0.8125rem; padding: 6px 8px;
}
.floatbar button:hover:not(:disabled) { color: #ff8a8a; }
.floatbar button svg { width: 16px; height: 16px; flex-shrink: 0; }
.floatbar button:disabled { opacity: .4; cursor: default; }
.floatbar .fmsg { color: #7ed321; font-size: 0.75rem; white-space: nowrap; }
.up-progress { height: 8px; border-radius: 999px; background: #2c2c2c; overflow: hidden; margin: 0 12px; }
.up-progress-fill { height: 100%; background: #e50914; border-radius: 999px; transition: width .2s; }
.watched-badge { position: absolute; bottom: 6px; left: 6px; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.72); color: #7ed321; }
.load-more { display: flex; align-items: center; justify-content: center; gap: 10px; padding: 10px 12px 22px; }
.warn-text { color: #e0a63c; }
.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 50; }
.dlg { background: #1c1c1c; border-radius: 10px; padding: 16px; min-width: 320px; max-width: 560px; max-height: 80vh; overflow: auto; }
.dlg h3 { margin: 0 0 8px; }
.taglist { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 12px; max-height: 30vh; overflow: auto; }
.collist { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 30vh; overflow: auto; }
.collist li { display: flex; justify-content: space-between; gap: 8px; align-items: center; background: #262626; border-radius: 8px; padding: 6px 10px; }
.del-warn { color: #ff8a8a; font-size: 0.875rem; margin: 0 0 8px; }
.del-sum { color: #e0a63c; font-size: 0.9375rem; margin: 0 0 8px; font-weight: bold; }
.danger-btn { border-color: #6e2b2b !important; color: #ff8a8a !important; }
</style>
