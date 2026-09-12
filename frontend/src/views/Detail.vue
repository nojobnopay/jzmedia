<template>
  <div class="detail" v-if="m">
    <div class="hero">
      <div v-if="m.poster_path" class="hero-bg" :style="{ backgroundImage: `url(${posterUrl(m.poster_path)})` }"></div>
      <div class="hero-inner">
        <div class="topbar">
          <button @click="$router.back()">‹ 返回</button>
          <span class="top-right">
            <span v-if="savedFlash" class="saved-flash">已保存</span>
            <button @click="toggleEdit">{{ editing ? '收起' : '编辑' }}</button>
          </span>
        </div>
        <div class="hero-main">
          <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" class="poster" />
          <div v-else class="poster poster-empty"><Spinner :size="22" /><span>海报补齐中</span></div>
          <div class="hero-info">
            <h2>{{ m.title }} <span v-if="m.year" class="year">({{ m.year }})</span><span v-if="m.edition" class="edition-chip">{{ m.edition }}</span><span v-if="m.spec" class="edition-chip spec">{{ m.spec }}</span><span v-if="m.needs_review" class="needs-review">待确认</span><span v-if="m.watched" class="watched-chip">✓已看</span></h2>
            <div v-if="hasScore(m.tmdb_rating) || hasScore(m.douban_rating) || hasScore(m.custom_rating)" class="rating-row">
              <span v-if="hasScore(m.tmdb_rating)" class="rate-chip tmdb"><span class="stars">{{ starRow(m.tmdb_rating) }}</span> {{ fmtScore(m.tmdb_rating) }} <span class="src">TMDB</span></span>
              <span v-if="hasScore(m.douban_rating)" class="rate-chip douban">豆瓣 {{ fmtScore(m.douban_rating) }}</span>
              <span v-if="hasScore(m.custom_rating)" class="rate-chip custom">自评 {{ fmtScore(m.custom_rating) }}</span>
            </div>
            <p v-if="metaLine" class="meta-line">{{ metaLine }}</p>
            <div v-if="(m.tags || []).length" class="tag-row">
              <span v-for="t in m.tags" :key="t" class="tag-chip">{{ t }}</span>
            </div>
            <div v-if="(m.collections || []).length" class="tag-row">
              <span v-for="c in m.collections" :key="c.id" class="col-chip" @click="$router.push('/c/' + c.id)">📁 {{ c.name }}</span>
            </div>
            <div v-if="hint && hint.collection_tmdb_id" class="hint-row">
              TMDB 系列：{{ hint.collection_name }}（库内 {{ hint.in_library_count }} 部）
              <button v-if="!hint.already_collected" @click="createFromSeries">一键建合集</button>
              <span v-else class="fhint">已收录</span>
              <span>{{ hintMsg }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="sections">
      <div class="body-grid">
        <div class="main-col">
          <section class="card-block">
            <h3>剧情简介</h3>
            <p v-if="m.overview_display" class="overview">{{ m.overview_display }}</p>
            <p v-else class="empty">暂无简介</p>
          </section>

          <section v-if="directors.length || actors.length" class="card-block">
            <h3>演职员</h3>
            <p v-if="directors.length" class="crew"><span class="role">导演</span>
              <span v-for="(p, i) in directors" :key="'d' + p.tmdb_id"><span class="actor-chip" @click="goPerson(p)">{{ p.name }}</span><span v-if="i < directors.length - 1"> </span></span>
            </p>
            <div v-if="actors.length" class="cast-wall">
              <div v-for="p in actors" :key="p.tmdb_id" class="cast-card" @click="goPerson(p)">
                <img v-if="p.avatar && p.avatar !== '-'" :src="posterUrl(p.avatar)" loading="lazy" />
                <div v-else class="avatar-fallback">{{ (p.name || '?').slice(0, 1) }}</div>
                <div class="cast-name">{{ p.name }}</div>
                <div v-if="showCharacter && p.character_name" class="cast-char">{{ p.character_name }}</div>
              </div>
            </div>
          </section>

          <details class="card-block files">
            <summary>文件<span v-if="m.version_count > 1">（共{{ m.version_count }}个版本）</span></summary>
            <ul><li v-for="v in m.versions" :key="v.id">{{ v.file_path }}<span v-if="v.edition">（{{ v.edition }}）</span><span v-if="v.spec">（{{ v.spec }}）</span></li></ul>
          </details>

          <details v-if="sideFiles && (sideFiles.extras.length || sideFiles.samples.length || sideFiles.subtitles.length)" class="card-block files">
            <summary>花絮 / 附带文件（只读）</summary>
            <p v-if="sideFiles.scoped === 'related'" class="hint">{{ sideFiles.hint }}</p>
            <ul v-if="sideFiles.extras.length"><li v-for="f in sideFiles.extras" :key="'e' + f.name">🎬 {{ f.name }}</li></ul>
            <ul v-if="sideFiles.samples.length"><li v-for="f in sideFiles.samples" :key="'s' + f.name">🎞 样片：{{ f.name }}</li></ul>
            <ul v-if="sideFiles.subtitles.length"><li v-for="f in sideFiles.subtitles" :key="'t' + f.name">💬 {{ f.name }}</li></ul>
          </details>
        </div>

        <aside class="side-col">
          <section class="card-block facts">
            <h3>影片信息</h3>
            <div v-if="m.original_title" class="fact"><span>原标题</span><span>{{ m.original_title }}</span></div>
            <div v-if="(m.genres || []).length" class="fact"><span>类型</span><span>{{ (m.genres || []).join(' / ') }}</span></div>
            <div v-if="m.region || originName" class="fact"><span>产地</span><span>{{ [m.region, originName].filter(Boolean).join(' · ') }}</span></div>
            <div v-if="m.year" class="fact"><span>年份</span><span>{{ m.year }}</span></div>
            <div v-if="originalMoved" class="fact"><span>原始文件</span><span>{{ m.original_file_path }} <button @click="goRestore">去恢复</button></span></div>
            <div v-if="m.tmdb_id" class="fact"><span>链接</span><span><a :href="`https://www.themoviedb.org/movie/${m.tmdb_id}`" target="_blank" rel="noopener">TMDB</a><a v-if="m.imdb_id" :href="`https://www.imdb.com/title/${m.imdb_id}/`" target="_blank" rel="noopener">IMDb</a></span></div>
          </section>
        </aside>
      </div>

      <section v-if="editing" class="card-block edit-panel">
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
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { hasScore, fmtScore, starRow } from '../ratings.js'
import Spinner from '../components/Spinner.vue'

const route = useRoute()
const router = useRouter()
const m = ref(null)
const sideFiles = ref(null)
const f = ref({ custom_rating: '', douban_rating: '', tags: '', overview_override: '', edition: '', spec: '', watched: false })
const msg = ref('')
const hint = ref(null)
const hintMsg = ref('')
const colList = ref([])
const joinColId = ref('')
const newColName = ref('')
const colMsg = ref('')
const editing = ref(false)
const savedFlash = ref(false)
let flashTimer = null
const actors = computed(() => (m.value?.persons || []).filter(p => p.role === 'actor'))
// TMDB character 是贡献者自由文本、不随语言翻译：非英语片里是英文描述/罗马音
//（如"Piggy"/"Deyunan (voice)"），只有原语言为英语时才可信展示
const showCharacter = computed(() => String(m.value?.original_language || '').toLowerCase().startsWith('en'))
const directors = computed(() => (m.value?.persons || []).filter(p => p.role === 'director'))
const originName = computed(() => {
  const mval = m.value || {}
  const code = mval.origin_country || ((mval.origin_countries || [])[0]) || ''
  if (!code) return ''
  const names = { CN: '中国大陆', HK: '香港', TW: '台湾', MO: '澳门', JP: '日本', KR: '韩国', US: '美国', GB: '英国', FR: '法国', DE: '德国' }
  return names[code] || code
})
const metaLine = computed(() => {
  const parts = []
  if (m.value?.year) parts.push(m.value.year)
  const origin = [m.value?.region, originName.value].filter(Boolean).join('·')
  if (origin) parts.push(origin)
  const genres = (m.value?.genres || []).slice(0, 3).join('/')
  if (genres) parts.push(genres)
  return parts.join(' · ')
})
const allTags = ref([])
const mq = ref('')
const cands = ref([])
const refreshMsg = ref('')
const bindingId = ref(null)
const refreshing = ref(false)
const searching = ref(false)

function syncForm() {
  f.value = {
    custom_rating: m.value.custom_rating ?? '',
    douban_rating: m.value.douban_rating ?? '',
    tags: (m.value.tags || []).join(','),
    overview_override: m.value.overview_override || '',
    edition: m.value.edition || '',
    spec: m.value.spec || '',
    watched: !!m.value.watched
  }
}
async function load() {
  m.value = await api('/api/movies/' + route.params.id)
  mq.value = m.value.title || ''
  syncForm()
  try {
    sideFiles.value = await api('/api/movies/' + route.params.id + '/files')
  } catch (e) { sideFiles.value = null }
  try {
    const d = await api('/api/facets')
    allTags.value = d.tags || []
  } catch (e) { /* 忽略 */ }
  try {
    hint.value = await api('/api/movies/' + route.params.id + '/collection-hint')
    if (!hint.value?.collection_tmdb_id) hint.value = null
  } catch (e) { hint.value = null }
  try {
    colList.value = (await api('/api/collections')).items || []
  } catch (e) { /* 忽略 */ }
}
async function reloadCollections() {
  try {
    m.value = await api('/api/movies/' + route.params.id)
    colList.value = (await api('/api/collections')).items || []
  } catch (e) { /* 忽略 */ }
}
async function joinCol() {
  if (!joinColId.value) return
  colMsg.value = ''
  try {
    await api(`/api/collections/${joinColId.value}/members`, {
      method: 'POST',
      body: JSON.stringify({ movie_ids: [Number(route.params.id)] })
    })
    colMsg.value = '已加入'
    joinColId.value = ''
    await reloadCollections()
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
      body: JSON.stringify({ name, member_ids: [Number(route.params.id)] })
    })
    colMsg.value = '已创建'
    newColName.value = ''
    await reloadCollections()
  } catch (e) {
    colMsg.value = '创建失败：' + e.message
  }
}
async function createFromSeries() {
  hintMsg.value = ''
  try {
    const d = await api('/api/collections/from-tmdb-series', {
      method: 'POST',
      body: JSON.stringify({ movie_id: Number(route.params.id) })
    })
    hintMsg.value = `已建「${d.name}」（${d.member_count} 部）`
    await reloadCollections()
  } catch (e) {
    hintMsg.value = '创建失败：' + e.message
  }
}
function toggleEdit() {
  if (!editing.value) {
    syncForm()
    msg.value = ''
  }
  editing.value = !editing.value
}
function cancelEdit() {
  syncForm()
  msg.value = ''
  editing.value = false
}
function flashSaved() {
  savedFlash.value = true
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { savedFlash.value = false }, 3000)
}
function num(v) {
  if (v === '' || v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : v
}
async function save() {
  msg.value = ''
  try {
    m.value = await api('/api/movies/' + route.params.id, {
      method: 'PATCH',
      body: JSON.stringify({
        custom_rating: num(f.value.custom_rating),
        douban_rating: num(f.value.douban_rating),
        tags: f.value.tags.split(/[,，、]/).map(s => s.trim()).filter(Boolean),
        overview_override: f.value.overview_override,
        edition: (f.value.edition || '').trim(),
        spec: (f.value.spec || '').trim(),
        watched: !!f.value.watched
      })
    })
    editing.value = false
    flashSaved()
  } catch (e) {
    msg.value = '保存失败：' + e.message
  }
}
function goPerson(p) {
  if (p && p.tmdb_id) router.push('/p/' + p.tmdb_id)
}
const originalMoved = computed(() => {
  const o = (m.value?.original_file_path || '').trim()
  return !!o && o !== m.value?.file_path
})
function goRestore() {
  router.push({ path: '/settings', query: { sec: 'sec-restore', ids: String(m.value.id) } })
}
async function tmdbSearch() {
  if (searching.value) return
  searching.value = true
  try {
    const d = await api('/api/tmdb/search?q=' + encodeURIComponent(mq.value))
    cands.value = d.items
  } finally {
    searching.value = false
  }
}
// 后台补齐（海报/头像）轮询：海报就绪即停，最多约 30s
async function waitForMedia(tries = 10) {
  for (let i = 0; i < tries; i++) {
    await new Promise(r => setTimeout(r, 3000))
    try {
      const cur = await api('/api/movies/' + route.params.id)
      m.value = cur
      syncForm()
      if (cur.poster_path) return
    } catch (e) { /* 忽略，继续轮询 */ }
  }
  await load()
}
async function bindMatch(tmdb_id) {
  if (bindingId.value) return
  bindingId.value = tmdb_id
  msg.value = '正在获取 TMDB 详情…'
  const oldRegion = m.value?.region || ''
  try {
    const r = await api('/api/movies/' + route.params.id + '/match', {
      method: 'POST',
      body: JSON.stringify({ tmdb_id })
    })
    await load()
    editing.value = false
    const regionNote = (m.value?.region && m.value.region !== oldRegion)
      ? `产地变为${m.value.region}，文件仍在旧分区，请到设置页用“搬到顶层”修复。` : ''
    if (r.background && (r.background.poster || r.background.avatars)) {
      msg.value = '已绑定，海报/演员补齐中…' + regionNote
      flashSaved()
      await waitForMedia()
      if (!regionNote) msg.value = ''
      else msg.value = regionNote
    } else {
      msg.value = regionNote
      flashSaved()
    }
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
    const r = await api('/api/movies/' + route.params.id + '/refresh', { method: 'POST' })
    refreshMsg.value = r.changed ? `已更新（${(r.affected_ids || []).length}个版本）` : '远端无变化'
    await load()
    if (r.changed) flashSaved()
    if (r.background && (r.background.poster || r.background.avatars)) {
      refreshMsg.value += '，图片补齐中…'
      await waitForMedia()
      refreshMsg.value = r.changed ? `已更新（${(r.affected_ids || []).length}个版本）` : '远端无变化'
    }
  } catch (e) {
    refreshMsg.value = '刷新失败：' + e.message
  } finally {
    refreshing.value = false
  }
}
onMounted(load)
</script>
<style scoped>
.detail { padding-bottom: 24px; }
.hero { position: relative; overflow: hidden; }
.hero-bg {
  position: absolute; inset: 0;
  background-size: cover; background-position: center 20%;
  filter: blur(28px) brightness(.45) saturate(1.2);
  transform: scale(1.15);
  -webkit-mask-image: linear-gradient(#000 30%, transparent);
  mask-image: linear-gradient(#000 30%, transparent);
}
.hero-inner { position: relative; padding: 12px; max-width: 1080px; }
.topbar { display: flex; justify-content: space-between; align-items: center; }
.top-right { display: flex; gap: 8px; align-items: center; }
.saved-flash { color: #7ed321; font-size: 0.875rem; }
.hero-main { display: flex; gap: 20px; margin-top: 12px; align-items: flex-start; }
.poster { width: 220px; border-radius: 8px; box-shadow: 0 8px 28px rgba(0,0,0,.55); }
.poster-empty { aspect-ratio: 2/3; display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: center; background: #262626; color: #888; font-size: 0.875rem; box-shadow: none; }
.hero-info { min-width: 0; }
.hero-info h2 { margin: 0 0 8px; font-size: 1.875rem; }
.hero-info .year { color: #aaa; font-weight: normal; font-size: 1.3125rem; }
.needs-review { color: #ff6b6b; font-size: 0.875rem; border: 1px solid #6e2b2b; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }
.edition-chip { color: #6ab0ff; font-size: 0.875rem; border: 1px solid #2b4a6e; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }
.edition-chip.spec { color: #7ed321; border-color: #3a5a1e; }
.meta-line { color: #aaa; font-size: 1rem; margin: 8px 0; }
.src { color: #888; font-weight: normal; }
.tag-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.tag-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px dashed #555; color: #ccc; }
.col-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px solid #2b4a6e; color: #6ab0ff; cursor: pointer; }
.watched-chip { color: #7ed321; font-size: 0.875rem; border: 1px solid #3a5a1e; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }
.hint-row { margin-top: 6px; color: #aaa; font-size: 0.875rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.hint-row .fhint { color: #777; font-size: 0.75rem; }
.sections { padding: 0 12px; max-width: 1080px; display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
.body-grid { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 12px; align-items: start; }
.main-col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.side-col { min-width: 0; }
@media (max-width: 860px) { .body-grid { grid-template-columns: 1fr; } }
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.overview { margin: 0; line-height: 1.8; color: #e6e6e6; font-size: 1rem; }
.empty { margin: 0; color: #777; font-size: 0.9375rem; }
.crew { margin: 8px 0; font-size: 0.9375rem; }
.role { color: #888; margin-right: 8px; font-size: 0.875rem; }
.actor-chip { display: inline-block; padding: 5px 14px; margin: 2px 4px 2px 0; border-radius: 999px; background: #262626; border: 1px solid #3a3a3a; cursor: pointer; font-size: 0.9375rem; }
.actor-chip:hover { border-color: #6ab0ff; color: #6ab0ff; }
.cast-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 12px; margin-top: 10px; }
.cast-card { cursor: pointer; min-width: 0; }
.cast-card img, .avatar-fallback { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 8px; display: block; background: #262626; }
.avatar-fallback { display: flex; align-items: center; justify-content: center; font-size: 2rem; color: #666; border: 1px solid #3a3a3a; }
.cast-name { font-size: 0.875rem; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cast-char { font-size: 0.75rem; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.facts .fact { display: flex; gap: 10px; font-size: 0.875rem; margin: 8px 0; }
.facts .fact span:first-child { color: #888; min-width: 48px; flex-shrink: 0; }
.facts a { color: #6ab0ff; margin-right: 10px; }
.files summary { cursor: pointer; color: #ccc; font-size: 0.9375rem; }
.files ul { color: #888; font-size: 0.875rem; }
.edit-panel .bar { padding: 6px 0; }
</style>
