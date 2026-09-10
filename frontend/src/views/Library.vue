<template>
  <div class="bar">
    <input v-model="q" placeholder="搜片名 / 演员 / 标签" @keyup.enter="applyAndLoad" />
    <button @click="applyAndLoad">搜索</button>
    <button @click="clearAll">全部</button>
    <button @click="doScan" :disabled="scanning">{{ scanning ? '刮削中…' : '扫描刮削' }}</button>
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
    <div class="frow" v-if="activeCount">
      <span class="fhint">已选 {{ activeCount }} 项 · 命中 {{ items.length }} 部</span>
      <button @click="clearFilters">清空筛选</button>
    </div>
  </div>

  <div class="grid">
    <div v-for="m in items" :key="m.id" class="card" @click="$router.push('/m/' + m.id)">
      <div class="poster-wrap">
        <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" loading="lazy" />
        <ScoreBadge :score="m.tmdb_rating" source="tmdb" />
      </div>
      <div class="t">{{ m.title }} <span v-if="m.year">({{ m.year }})</span><span v-if="m.version_count > 1"> ×{{ m.version_count }}</span><span v-if="m.needs_review"> [待确认]</span><span v-if="hasScore(m.custom_rating)" class="custom-mini">♥{{ fmtScore(m.custom_rating) }}</span><br v-if="m.region || (m.genres || []).length" /><span v-if="m.region" class="meta">{{ m.region }}</span><span v-if="(m.genres || []).length" class="meta"> {{ (m.genres || []).slice(0, 2).join('/') }}</span></div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import ScoreBadge from '../components/ScoreBadge.vue'
import { hasScore, fmtScore } from '../ratings.js'

const route = useRoute()
const router = useRouter()

const q = ref('')
const items = ref([])
const msg = ref('')
const scanning = ref(false)
const facets = ref({ genres: [], regions: [], countries: [], years: [], decades: [], tags: [], ratings: { tmdb: [], douban: [], custom: [] } })
const sel = ref({ genres: [], regions: [], countries: [], years: [], decades: [], tags: [], rating: null, ratingSource: 'tmdb' })
const yearPick = ref('')

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
  (sel.value.rating == null ? 0 : 1))

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
function syncUrl() {
  const query = {}
  if (q.value.trim()) query.q = q.value.trim()
  if (sel.value.genres.length) query.genre = sel.value.genres.join(',')
  if (sel.value.regions.length) query.region = sel.value.regions.join(',')
  if (sel.value.countries.length) query.country = sel.value.countries.join(',')
  if (sel.value.years.length) query.year = sel.value.years.join(',')
  if (sel.value.decades.length) query.decade = sel.value.decades.join(',')
  if (sel.value.tags.length) query.tag = sel.value.tags.join(',')
  if (sel.value.rating != null) {
    query.min_rating = String(sel.value.rating)
    if (sel.value.ratingSource !== 'tmdb') query.rating_source = sel.value.ratingSource
  }
  router.replace({ path: '/', query })
}
function readUrl() {
  const s = (v) => v ? String(v).split(',').map(x => x.trim()).filter(Boolean) : []
  const src = String(route.query.rating_source || 'tmdb')
  q.value = route.query.q || ''
  sel.value = {
    genres: s(route.query.genre),
    regions: s(route.query.region),
    countries: s(route.query.country),
    years: s(route.query.year),
    decades: s(route.query.decade),
    tags: s(route.query.tag),
    rating: route.query.min_rating != null && route.query.min_rating !== '' ? Number(route.query.min_rating) : null,
    ratingSource: ['tmdb', 'douban', 'custom'].includes(src) ? src : 'tmdb',
  }
}
function buildParams() {
  const p = new URLSearchParams()
  if (q.value.trim()) p.set('q', q.value.trim())
  // 选了具体国家时大区自动让位（后端两者是AND，避免华语+US这种空交集）
  const useRegion = sel.value.countries.length ? [] : sel.value.regions
  for (const [key, vals] of [['genre', sel.value.genres], ['region', useRegion],
      ['country', sel.value.countries], ['year', sel.value.years],
      ['decade', sel.value.decades], ['tag', sel.value.tags]]) {
    for (const v of vals) p.append(key, v)
  }
  if (sel.value.rating != null) {
    p.set('min_rating', String(sel.value.rating))
    p.set('rating_source', sel.value.ratingSource)
  }
  return p.toString()
}
async function load() {
  const qs = buildParams()
  const d = await api('/api/search?' + qs)
  items.value = d.items
}
async function applyAndLoad() {
  syncUrl()
  await load()
}
async function showAll() {
  q.value = ''
  sel.value = { genres: [], regions: [], countries: [], years: [], decades: [], tags: [], rating: null, ratingSource: 'tmdb' }
  syncUrl()
  await load()
}
async function clearFilters() {
  sel.value = { genres: [], regions: [], countries: [], years: [], decades: [], tags: [], rating: null, ratingSource: 'tmdb' }
  await applyAndLoad()
}
async function clearAll() { await showAll() }
async function loadFacets() {
  try { facets.value = await api('/api/facets') } catch (e) { /* 库空时忽略 */ }
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
onMounted(async () => {
  readUrl()
  await loadFacets()
  await load()
})
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
.meta { color: #888; font-size: 0.75rem; }
.custom-mini { color: #ff6b6b; font-size: 0.75rem; }
</style>
