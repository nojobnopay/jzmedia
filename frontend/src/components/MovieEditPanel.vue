<template>
      <section class="card-block edit-panel">
        <template v-if="mode === 'edit'"><h3>编辑资料</h3>
        <div class="bar"><input v-model="f.edition" placeholder="版本（如 导演剪辑版，留空为普通版）" style="flex:1" /></div>
        <div class="bar"><input v-model="f.spec" placeholder="规格/备注（如 杜比视界/蓝光，留空自动识别）" style="flex:1" /></div>
        <div class="bar">
          <label>自评 <input v-model="f.custom_rating" placeholder="0-10" style="width:70px" /></label>
          <label>豆瓣 <input v-model="f.douban_rating" placeholder="0-10" style="width:70px" /></label>
        </div>
        <div class="bar"><input v-model="f.tags" placeholder="标签，逗号分隔" style="flex:1" list="taglist" /></div>
        <datalist id="taglist"><option v-for="t in allTags" :key="t.value" :value="t.value" /></datalist>
        <div class="bar"><textarea v-model="f.overview_override" placeholder="简介覆盖（留空用刮削简介）" rows="3" style="flex:1"></textarea></div>
        <div class="bar"><button @click="save">保存</button><button @click="cancelEdit">取消</button><span>{{ msg }}</span></div>
        </template>
        <template v-else>
        <h3>手动匹配 <span v-if="movie.tmdb_id">(当前TMDB {{ movie.tmdb_id }})</span></h3>
        <div class="bar">
          <input v-model="mq" placeholder="输入片名查找资料" aria-label="搜索匹配" style="flex:1" />
          <button @click="tmdbSearch" :disabled="searching || !!bindingId"><Spinner v-if="searching" />{{ searching ? '搜索中…' : '搜索匹配' }}</button>
        </div>
        <ul>
          <li v-for="c in cands" :key="candKey(c)">
            <span class="src-badge">{{ c.source ? srcLabel(c.source) : 'TMDB' }}</span>
            {{ c.title }} ({{ (c.release_date || c.year || '').toString().slice(0, 4) }})
            <template v-if="c.vote_average != null">★{{ c.vote_average }}</template>
            <button v-if="c.tmdb_id" @click="bindMatch(c.tmdb_id)" :disabled="!!bindingId"><Spinner v-if="bindingId === c.tmdb_id" />{{ bindingId === c.tmdb_id ? '绑定中…' : '绑定' }}</button>
            <button v-else-if="isExternal(c)" @click="bindExternal(c)" :disabled="!!bindingId"><Spinner v-if="bindingId === candKey(c)" />{{ bindingId === candKey(c) ? '绑定中…' : '绑定外源' }}</button>
          </li>
        </ul>
        <p class="fhint">候选来源：TMDB 无凭据/不可用时自动回退本地索引、Wikidata、TVmaze（剧）、Bangumi 与 NFO；外源绑定不依赖 TMDB Token。</p>
        <p v-if="msg" role="status">{{ msg }}</p><button @click="cancelEdit">收起匹配</button>
        </template>
      </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
import Spinner from './Spinner.vue'

// 详情页编辑面板（评审 R05-Q4：自 Detail.vue 抽出）：字段编辑 / 合集加入 / TMDB 重匹配。
// 自身只发改动信号，重载与归档引导由父页处理。
const props = defineProps({
  movie: { type: Object, required: true },
  movieId: { type: Number, required: true },
  mode: { type: String, default: 'edit' }
})
const emit = defineEmits(['close', 'saved', 'matched'])

const f = ref({
  custom_rating: '', douban_rating: '', tags: '', overview_override: '',
  edition: '', spec: ''
})
const msg = ref('')
const allTags = ref([])
const mq = ref(props.movie.title || '')
const cands = ref([])
const bindingId = ref(null)
const searching = ref(false)

function cancelEdit() {
  emit('close')
}
function syncForm() {
  const m = props.movie || {}
  f.value = {
    custom_rating: m.custom_rating ?? '',
    douban_rating: m.douban_rating ?? '',
    tags: (m.tags || []).join(','),
    overview_override: m.overview_override || '',
    edition: m.edition || '',
    spec: m.spec || ''
  }
}
function num(v) {
  if (v === '' || v === null || v === undefined) return null
  const x = Number(v)
  return Number.isFinite(x) ? x : null
}
async function save() {
  msg.value = ''
  try {
    await api('/api/movies/' + props.movieId, {
      method: 'PATCH',
      body: JSON.stringify({
        custom_rating: num(f.value.custom_rating),
        douban_rating: num(f.value.douban_rating),
        tags: f.value.tags.split(/[,，、]/).map(t => t.trim()).filter(Boolean),
        overview_override: f.value.overview_override,
        edition: (f.value.edition || '').trim(),
        spec: (f.value.spec || '').trim()
      })
    })
    emit('saved')
  } catch (e) {
    msg.value = '保存失败：' + e.message
  }
}

async function tmdbSearch() {
  if (searching.value) return
  searching.value = true
  msg.value = ''
  try {
    const d = await api('/api/tmdb/search?q=' + encodeURIComponent(mq.value))
    cands.value = d.items
  } catch (e) {
    msg.value = '搜索失败：' + e.message
  } finally {
    searching.value = false
  }
}
async function bindMatch(tmdb_id) {
  if (bindingId.value) return
  bindingId.value = tmdb_id
  msg.value = '正在获取 TMDB 详情…'
  const oldRegion = props.movie?.region || ''
  try {
    const r = await api('/api/movies/' + props.movieId + '/match', {
      method: 'POST',
      body: JSON.stringify({ tmdb_id })
    })
    emit('matched', { oldRegion, background: r.background || {} })
  } catch (e) {
    msg.value = '绑定失败：' + e.message
  } finally {
    bindingId.value = null
  }
}
// 外部元数据候选（P2.5）：无 TMDB ID 时走 bind-external，不依赖 Token
const EXTERNAL_SOURCES = ['wikidata', 'tvmaze', 'bgm', 'douban', 'nfo']
const SOURCE_LABELS = {
  wikidata: 'Wikidata', tvmaze: 'TVmaze', bgm: 'Bangumi',
  douban: '豆瓣', nfo: 'NFO', local: '本地', library: '本地库'
}
function candKey(c) {
  return c.tmdb_id ? 'tmdb:' + c.tmdb_id : (c.source || '') + ':' + (c.source_id || c.title || '')
}
function srcLabel(s) { return SOURCE_LABELS[s] || s }
function isExternal(c) { return !c.tmdb_id && EXTERNAL_SOURCES.includes(c.source) }
async function bindExternal(c) {
  if (bindingId.value) return
  bindingId.value = candKey(c)
  msg.value = '正在获取外部详情…'
  const oldRegion = props.movie?.region || ''
  try {
    const r = await api('/api/movies/' + props.movieId + '/bind-external', {
      method: 'POST',
      body: JSON.stringify({ source: c.source, source_id: c.source_id })
    })
    emit('matched', { oldRegion, background: r.background || {} })
  } catch (e) {
    msg.value = '绑定失败：' + e.message
  } finally {
    bindingId.value = null
  }
}

onMounted(async () => {
  syncForm()
  try {
    const d = await api('/api/facets')
    allTags.value = (d.tags || []).map(t => (typeof t === 'object' ? t : { value: t }))
  } catch (e) { /* 忽略 */ }
})
</script>

<style scoped>
.edit-panel .bar { padding: 6px 0; }
.edit-panel .src-badge { display: inline-block; padding: 0 5px; border-radius: 3px;
  background: #333; border: 1px solid #444; font-size: 0.75rem; margin-right: 4px; }
.edit-panel .fhint { color: #888; font-size: 0.8125rem; }
</style>

<style scoped>
.bar { flex-wrap: wrap; }
input, textarea { min-width: 0; }
</style>
