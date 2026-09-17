<template>
      <section class="card-block edit-panel">
        <h3>手动编辑</h3>
        <div class="bar"><input v-model="f.edition" placeholder="版本（如 导演剪辑版，留空为普通版）" style="flex:1" /></div>
        <div class="bar"><input v-model="f.spec" placeholder="规格/备注（如 杜比视界/蓝光，留空自动识别）" style="flex:1" /></div>
        <div class="bar">
          <label>自评 <input v-model="f.custom_rating" placeholder="0-10" style="width:70px" /></label>
          <label>豆瓣 <input v-model="f.douban_rating" placeholder="0-10" style="width:70px" /></label>
        </div>
        <div class="bar"><input v-model="f.tags" placeholder="标签，逗号分隔" style="flex:1" list="taglist" /></div>
        <datalist id="taglist"><option v-for="t in allTags" :key="t.value" :value="t.value" /></datalist>
        <div class="bar"><label><input type="checkbox" v-model="f.watched" /> 已观看</label></div>
        <div class="bar"><textarea v-model="f.overview_override" placeholder="简介覆盖（留空用刮削简介）" rows="3" style="flex:1"></textarea></div>
        <div class="bar"><button @click="save">保存</button><button @click="cancelEdit">取消</button><span>{{ msg }}</span></div>
        <h3>所属合集</h3>
        <div class="bar">
          <select v-model="joinColId" style="flex:1">
            <option value="">选择合集…</option>
            <option v-for="c in colList" :key="c.id" :value="c.id">{{ c.name }}（{{ c.member_count }}）</option>
          </select>
          <button @click="joinCol" :disabled="!joinColId">加入</button>
        </div>
        <div class="bar">
          <input v-model="newColName" placeholder="新建合集名（含本片）" style="flex:1" />
          <button @click="createCol" :disabled="!newColName.trim()">创建</button>
          <span>{{ colMsg }}</span>
        </div>
        <h3>手动匹配 <span v-if="m.tmdb_id">(当前TMDB {{ m.tmdb_id }})</span></h3>
        <div class="bar">
          <button @click="refreshTmdb" :disabled="refreshing || !!bindingId"><Spinner v-if="refreshing" />{{ refreshing ? '刷新中…' : '刷新TMDB（有变化才更新）' }}</button>
          <span>{{ refreshMsg }}</span>
        </div>
        <div class="bar">
          <input v-model="mq" placeholder="TMDB搜关键词" style="flex:1" />
          <button @click="tmdbSearch" :disabled="searching || !!bindingId"><Spinner v-if="searching" />{{ searching ? '搜索中…' : '搜TMDB' }}</button>
        </div>
        <ul>
          <li v-for="c in cands" :key="c.tmdb_id">
            {{ c.title }} ({{ (c.release_date || '').slice(0, 4) }}) ★{{ c.vote_average }}
            <button @click="bindMatch(c.tmdb_id)" :disabled="!!bindingId || refreshing"><Spinner v-if="bindingId === c.tmdb_id" />{{ bindingId === c.tmdb_id ? '绑定中…' : '绑定' }}</button>
          </li>
        </ul>
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
  movieId: { type: Number, required: true }
})
const emit = defineEmits(['close', 'saved', 'changed', 'matched', 'refreshed'])

const f = ref({
  custom_rating: '', douban_rating: '', tags: '', overview_override: '',
  edition: '', spec: '', watched: false
})
const msg = ref('')
const allTags = ref([])
const mq = ref(props.movie.title || '')
const cands = ref([])
const refreshMsg = ref('')
const bindingId = ref(null)
const refreshing = ref(false)
const searching = ref(false)
const colList = ref([])
const joinColId = ref('')
const newColName = ref('')
const colMsg = ref('')

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
    spec: m.spec || '',
    watched: !!m.watched
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
        spec: (f.value.spec || '').trim(),
        watched: !!f.value.watched
      })
    })
    emit('saved')
  } catch (e) {
    msg.value = '保存失败：' + e.message
  }
}

async function refreshCollections() {
  try {
    colList.value = (await api('/api/collections')).items || []
  } catch (e) { /* 忽略 */ }
}
async function joinCol() {
  if (!joinColId.value) return
  colMsg.value = ''
  try {
    await api(`/api/collections/${joinColId.value}/members`, {
      method: 'POST',
      body: JSON.stringify({ movie_ids: [props.movieId] })
    })
    colMsg.value = '已加入'
    joinColId.value = ''
    await refreshCollections()
    emit('changed')
  } catch (e) {
    colMsg.value = '加入失败：' + e.message
  }
}
async function createCol() {
  const name = newColName.value.trim()
  if (!name) return
  colMsg.value = ''
  try {
    await api('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name, member_ids: [props.movieId] })
    })
    colMsg.value = '已创建'
    newColName.value = ''
    await refreshCollections()
    emit('changed')
  } catch (e) {
    colMsg.value = '创建失败：' + e.message
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
async function refreshTmdb() {
  if (refreshing.value) return
  refreshing.value = true
  refreshMsg.value = '刷新中…'
  try {
    const r = await api('/api/movies/' + props.movieId + '/refresh', { method: 'POST' })
    refreshMsg.value = r.changed ? `已更新（${(r.affected_ids || []).length}个版本）` : '远端无变化'
    if (r.background && (r.background.poster || r.background.avatars)) {
      refreshMsg.value += '，图片补齐中…'
    }
    emit('refreshed')
  } catch (e) {
    refreshMsg.value = '刷新失败：' + e.message
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  syncForm()
  await refreshCollections()
  try {
    const d = await api('/api/facets')
    allTags.value = (d.tags || []).map(t => (typeof t === 'object' ? t : { value: t }))
  } catch (e) { /* 忽略 */ }
})
</script>

<style scoped>
.edit-panel .bar { padding: 6px 0; }
</style>
