<template>
  <div class="detail" v-if="m">
    <div class="hero">
      <div v-if="m.poster_path" class="hero-bg" :style="{ backgroundImage: `url(${posterUrl(m.poster_path)})` }"></div>
      <div class="hero-inner">
        <div class="topbar">
          <button @click="$router.back()">‹ 返回</button>
          <span class="top-right">
            <span v-if="savedFlash" class="saved-flash">已保存</span>
            <span v-if="buildVer" class="ver-tag" :title="'后端构建 ' + buildVer">构建 {{ buildVer }}</span>
            <button @click="toggleEdit">{{ editing ? '收起' : '编辑' }}</button>
          </span>
        </div>
        <div class="hero-main">
          <img v-if="m.poster_path" :src="posterUrl(m.poster_path)" class="poster zoomable" title="查看大图" @click="openPoster" />
          <div v-else class="poster poster-empty"><Spinner :size="22" /><span>海报补齐中</span></div>
          <div class="hero-info">
            <h2>{{ m.title }} <span v-if="m.year" class="year">({{ m.year }})</span><span v-if="m.edition" class="edition-chip">{{ m.edition }}</span><span v-if="m.spec" class="edition-chip spec">{{ m.spec }}</span><span v-if="m.needs_review" class="needs-review">待确认</span><span v-if="m.watched" class="watched-chip">✓已看</span></h2>
            <div v-if="hasScore(m.tmdb_rating) || hasScore(m.douban_rating) || hasScore(m.custom_rating)" class="rating-row">
              <span v-if="hasScore(m.tmdb_rating)" class="rate-chip tmdb"><span class="stars">{{ starRow(m.tmdb_rating) }}</span> {{ fmtScore(m.tmdb_rating) }} <span class="src">TMDB</span></span>
              <span v-if="hasScore(m.douban_rating)" class="rate-chip douban">豆瓣 {{ fmtScore(m.douban_rating) }}</span>
              <span v-if="hasScore(m.custom_rating)" class="rate-chip custom">自评 {{ fmtScore(m.custom_rating) }}</span>
            </div>
            <p v-if="metaLine" class="meta-line">{{ metaLine }}</p>
            <div v-if="mediaBadge || mediaUnplayable || resumeText || noFfmpeg" class="media-row">
              <span v-if="mediaBadge" class="media-badge">{{ mediaBadge }}</span>
              <span v-if="mediaUnplayable" class="media-warn" :title="mediaError">无效文件，无法播放</span>
              <span v-if="noFfmpeg" class="media-warn" title="服务器缺 ffmpeg：转码/重封装不可用，直链与电视播放不受影响">转码不可用（缺 ffmpeg）</span>
              <span v-if="resumeText" class="resume-hint">{{ resumeText }}</span>
            </div>
            <div v-else-if="mediaLoading" class="media-row"><span class="media-loading">媒体信息探测中…</span></div>
            <div class="play-row">
              <button class="play-main" :disabled="heroBlocked" :title="heroBlockTip" @click="openHeroPlay">▶ 播放</button>
              <select v-if="(m.versions || []).length > 1" v-model.number="heroVid" class="ver-sel">
                <option v-for="v in m.versions" :key="v.id" :value="v.id" :disabled="!!verBlocked[v.id]">
                  {{ verLabel(v) }}{{ verBlocked[v.id] ? '（无效）' : (verFriendly(v.id) ? ' ★浏览器友好' : '') }}
                </option>
              </select>
              <span v-if="heroResume" class="resume-hint">{{ heroResume }}</span>
              <button v-if="!heroBlocked && !verFriendly(heroVid)" class="pre-btn"
                :disabled="!!preJob" :title="'夜间/闲时把本片转好存着，完工后点播即静态秒播'"
                @click="startPrewarm">{{ preJob ? '预转码中…' : '预转码720p' }}</button>
              <span v-if="preMsg" class="resume-hint">{{ preMsg }}</span>
            </div>
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

          <details class="card-block files" open>
            <summary>文件<span v-if="m.version_count > 1">（共{{ m.version_count }}个版本）</span></summary>
            <ul class="ver-list"><li v-for="v in m.versions" :key="v.id" class="f-row">
              <span class="f-name">{{ baseName(v.file_path) }}<span v-if="v.edition">（{{ v.edition }}）</span><span v-if="v.spec">（{{ v.spec }}）</span></span>
              <span class="f-acts"><a :href="blobUrl(v.file_path)" :download="baseName(v.file_path)">下载</a><button v-if="verBlocked[v.id]" disabled :title="verErr[v.id] || '无效文件'">无效</button><button v-else @click="openStream(v)">播放</button><span v-if="verFriendly(v.id)" class="friendly-chip" title="浏览器可直播，几乎不占 NAS 算力">★</span><span v-else-if="verMethod[v.id]==='transcode'" class="trans-chip" title="浏览器需转码，较耗 NAS 算力">转码</span></span>
            </li></ul>
            <div v-for="g in fileGroups" :key="g.key">
              <p v-if="g.items.length" class="hint">{{ g.label }}</p>
              <ul v-if="g.items.length">
                <li v-for="f in g.items" :key="g.key + f.name" class="f-row">
                  <span class="f-name">{{ f.name }}</span>
                  <span class="f-size">{{ fmtSize(f.size) }}</span>
                  <span class="f-acts">
                    <a :href="blobUrl(f.rel || f.name)" :download="baseName(f.rel || f.name)">下载</a>
                    <button v-if="isVideo(f.name)" @click="openPlayer(f)">播放</button>
                    <button v-else-if="pvKindOf(f.name)" @click="openPlayer(f)">预览</button>
                    <button v-if="delArm[f.rel || f.name] == null" @click="doFileDelete(f)">删除</button>
                    <button v-else @click="doFileDeleteConfirm(f)" class="danger">确认删除正片</button>
                  </span>
                </li>
              </ul>
            </div>
            <p v-if="delMsg" class="hint warn">{{ delMsg }}</p>
          </details>

          <details class="card-block tvplay">
            <summary>电视播放（Kodi/外部播放器，原盘直通零转码）</summary>
            <ul class="ver-list"><li v-for="v in m.versions" :key="'tv' + v.id" class="f-row">
              <span class="f-name">{{ baseName(v.file_path) }}</span>
              <span class="f-acts"><button @click="copyTvUrl(v)">复制直链</button></span>
            </li></ul>
            <p class="hint">电视端 Kodi 打开此链接即播原盘（含杜比视界），不耗 NAS 算力；浏览器在线播请用上方 ▶ 播放。{{ tvMsg }}</p>
          </details>

          <section class="card-block">
            <h3>上传文件 <span class="q-tip" tabindex="0">?<span class="q-bubble">适合上传：海报/剧照（jpg/png）、音乐/原声（mp3/flac）、剧本/字幕（txt/srt/ass/pdf）、花絮视频（自动进 extras/）；正片新版本视频也可上传，会自动刮削入库。&gt;2GB 建议在局域网操作，可随时取消。</span></span></h3>
            <div class="bar up-row">
              <input type="file" ref="upInput" :disabled="!!upCtl" />
              <select v-model="upSubdir" :disabled="!!upCtl">
                <option value="">片目录</option>
                <option value="extras">extras/</option>
              </select>
              <button @click="doUpload" :disabled="!!upCtl">上传</button>
              <button v-if="upCtl" @click="cancelUpload">取消</button>
              <span v-if="upPct !== null">{{ upPct }}%</span>
              <span>{{ upMsg }}</span>
            </div>
            <div v-if="upPct !== null" class="up-bar"><i :style="{ width: upPct + '%' }"></i></div>
          </section>
        </div>

        <aside class="side-col">
          <section class="card-block facts">
            <h3>影片信息</h3>
            <div v-if="m.original_title" class="fact"><span>原标题</span><span>{{ m.original_title }}</span></div>
            <div v-if="(m.genres || []).length" class="fact"><span>类型</span><span>{{ (m.genres || []).join(' / ') }}</span></div>
            <div v-if="m.region || originName" class="fact"><span>产地</span><span>{{ [m.region, originName].filter(Boolean).join(' · ') }}</span></div>
            <div v-if="m.year" class="fact"><span>年份</span><span>{{ m.year }}</span></div>
            <div v-if="originalMoved" class="fact"><span>原始文件</span><span class="fact-val" :title="m.original_file_path">{{ m.original_file_path }} <button @click="goRestore">去恢复</button></span></div>
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

    <div v-if="pvName" class="dlg-mask" @click.self="closePlayer">
      <div class="dlg pv-dlg">
        <h3>{{ pvKind === 'video' ? '播放' : '预览' }}：{{ pvName }}</h3>
        <video v-if="pvKind === 'video'" :src="pvUrl" controls autoplay preload="metadata" class="pv-video" @error="pvErr = true"></video>
        <img v-else-if="pvKind === 'image'" :src="pvUrl" class="pv-img" />
        <iframe v-else-if="pvKind === 'pdf'" :src="pvUrl" class="pv-pdf"></iframe>
        <pre v-else-if="pvKind === 'text'" class="pv-text">{{ pvText }}</pre>
        <p v-if="pvErr" class="hint warn">文件为空或损坏，无法播放，请下载检查</p>
        <div class="bar"><a :href="pvUrl" :download="baseName(pvName)">下载原文件</a><button @click="closePlayer">关闭</button></div>
      </div>
    </div>

    <PlayerModal v-if="playVid" :versionId="playVid" :title="playTitle"
      @close="closeStream" @watched="onPlayEnded" />

    <div v-if="posterDlg" class="dlg-mask" @click.self="closePoster">
      <div class="dlg pv-dlg poster-dlg">
        <img :src="posterBig" class="pv-img poster-big" />
        <div class="bar"><span class="hint">{{ posterHi ? '高清原图' : '标清预览（原图加载中或不可用）' }}</span><a :href="posterBig" :download="baseName(posterBig)">下载</a><button @click="closePoster">关闭</button></div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, apiUpload, posterUrl } from '../api.js'
import { hasScore, fmtScore, starRow } from '../ratings.js'
import Spinner from '../components/Spinner.vue'
import PlayerModal from '../components/PlayerModal.vue'

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
// 在线播放 P0：版本媒体徽章 + 断点提示（播放器弹窗在 P1–P4 接入）
const mediaInfo = ref(null)
const mediaLoading = ref(false)
const mediaError = ref('')
const noFfmpeg = ref(false)
const progressInfo = ref(null)
const buildVer = ref('')
const mediaBadge = computed(() => {
  const mi = mediaInfo.value
  if (!mi || !mi.playable) return ''
  const parts = []
  if (mi.duration_text) parts.push(mi.duration_text)
  const h = Number(mi.height) || 0
  if (h >= 2000) parts.push('4K')
  else if (h >= 1000) parts.push('1080p')
  else if (h >= 650) parts.push('720p')
  else if (h > 0) parts.push(h + 'p')
  if (mi.vcodec) parts.push(String(mi.vcodec).toUpperCase())
  const na = (mi.audio || []).length
  if (na > 1) parts.push(`音频${na}轨`)
  else if (na === 1 && mi.acodec) parts.push(String(mi.acodec).toUpperCase())
  const ns = (mi.subs || []).length
  if (ns) parts.push(`字幕${ns}`)
  return parts.join(' · ')
})
const mediaUnplayable = computed(() => mediaInfo.value && !mediaInfo.value.playable)
const resumeText = computed(() => {
  const p = progressInfo.value
  if (!p || !(Number(p.position) > 15)) return ''
  const dur = Number(p.duration) || Number(mediaInfo.value?.duration) || 0
  const remain = dur - Number(p.position)
  if (dur > 0 && (remain / dur < 0.05 || remain < 300)) return ''
  return `上次看到 ${p.position_text || ''}`
})
async function loadMedia() {
  mediaLoading.value = true
  mediaError.value = ''
  // P-B：一次取齐全版本（媒体+决策+最优版），替代逐版本 media 轮询
  try {
    const agg = await api(`/api/stream/versions?movie_id=${route.params.id}&quality=original`)
    verList.value = agg.versions || []
    bestVid.value = agg.best_version_id || null
    const cur = verList.value.find(x => Number(x.version_id) === Number(route.params.id))
    mediaInfo.value = cur && cur.playable
      ? { playable: true, duration_text: cur.duration_text, duration: cur.duration,
          height: cur.height, vcodec: cur.vcodec, acodec: '',
          audio: new Array(cur.audio_count).fill({}), subs: new Array(cur.sub_count).fill({}) }
      : (cur ? { playable: false, probe_error: cur.probe_error } : null)
    if (mediaInfo.value && !mediaInfo.value.playable) {
      mediaError.value = mediaInfo.value.probe_error || '无法识别媒体流'
    }
    for (const x of verList.value) {
      if (!x.playable || x.method === 'blocked') {
        verBlocked.value[x.version_id] = true
        verErr.value[x.version_id] = x.probe_error || '无法识别媒体流'
      }
      verMethod.value[x.version_id] = x.method
    }
    // hero 默认选中浏览器最优版（无效时回落当前行）
    heroVid.value = bestVid.value || Number(route.params.id)
  } catch (e) {
    mediaInfo.value = null
    mediaError.value = String(e.message || e)
    heroVid.value = Number(route.params.id)
  } finally {
    mediaLoading.value = false
  }
  try {
    const p = await api(`/api/stream/progress?version_id=${heroVid.value}`)
    progressInfo.value = (p && Number(p.position) > 0) ? p : null
  } catch (e) { progressInfo.value = null }
}
// 在线播放 P1：版本选播弹窗（播放单位=版本行 id）+ 播完标已看（阈值逻辑 P3 进弹窗内）
const playVid = ref(null)
const playTitle = ref('')
const verBlocked = ref({})
const verErr = ref({})
const verMethod = ref({})
const verList = ref([])
const bestVid = ref(null)
// “浏览器友好”= direct/remux（零/近零 CPU），转码版不打标
const verFriendly = (id) => ['direct', 'remux'].includes(verMethod.value[id])
// P4 hero 主播放键：默认当前行，多版本可下拉切换（无效版本禁用）
const heroVid = ref(null)
const heroBlocked = computed(() => !!verBlocked.value[heroVid.value])
const heroBlockTip = computed(() => verErr.value[heroVid.value] || '')
const heroResume = computed(() => resumeText.value)
function verLabel(v) {
  const extra = [v.edition, v.spec].filter(Boolean).join('·')
  return baseName(v.file_path) + (extra ? `（${extra}）` : '')
}
function openHeroPlay() {
  if (heroBlocked.value || !heroVid.value) return
  const v = (m.value?.versions || []).find(x => Number(x.id) === Number(heroVid.value))
  openStream(v || { id: heroVid.value, file_path: m.value?.file_path || '' })
}
function openStream(v) {
  playTitle.value = baseName(v.file_path)
  playVid.value = Number(v.id)
}
// 预转码：闲时把本片转完存静态，完工后点播秒播；只给需转码版显示
const preJob = ref(null)
const preMsg = ref('')
let preTimer = 0
async function startPrewarm() {
  if (preJob.value || !heroVid.value) return
  preMsg.value = ''
  try {
    const r = await api('/api/stream/prewarm', {
      method: 'POST',
      body: JSON.stringify({ version_id: Number(heroVid.value), quality: '720p', audio: 0 }),
    })
    preJob.value = r.job_id
    preMsg.value = '已开始，后台转码中…'
    preTimer = setInterval(async () => {
      if (!preJob.value) return
      try {
        const s = await api(`/api/stream/prewarm/${preJob.value}`)
        const exp = Number(s.expected) || 0
        const got = Number(s.segments) || 0
        if (s.status === 'done') {
          preMsg.value = '已就绪，点播即秒播'
          preJob.value = null
          clearInterval(preTimer)
          loadMedia()
        } else if (s.status === 'failed') {
          preMsg.value = '失败：' + (s.error || '未知').slice(0, 80)
          preJob.value = null
          clearInterval(preTimer)
        } else if (exp > 0) {
          preMsg.value = `转码中 ${Math.floor((got / exp) * 100)}%（${got}/${exp}）`
        }
      } catch (e) { /* 轮询失败下次继续 */ }
    }, 5000)
  } catch (e) {
    preMsg.value = '启动失败：' + e.message
  }
}
async function closeStream() {
  // 关播即刷新断点：详情页“上次看到”不再等手动刷新（仅刷新当前选中版本）
  const v = playVid.value
  playVid.value = null
  if (!v || Number(v) !== Number(heroVid.value)) return
  try {
    const p = await api(`/api/stream/progress?version_id=${v}`)
    progressInfo.value = (p && Number(p.position) > 0) ? p : null
  } catch (e) { /* 忽略 */ }
}
// P-D：电视原盘直链（Kodi/外部播放器直通，零转码；blob 本就支持 Range）
const tvMsg = ref('')
async function copyTvUrl(v) {
  tvMsg.value = ''
  const url = location.origin + `/api/movies/${v.id}/blob?name=${encodeURIComponent(v.file_path)}`
  try {
    await navigator.clipboard.writeText(url)
    tvMsg.value = '已复制，在 Kodi 里打开该链接即播'
  } catch (e) {
    tvMsg.value = url
  }
}
async function onPlayEnded() {
  // 海报粒度：同片全版本同步标已看（与批量 watched 展开语义一致）
  try {
    const ids = [...new Set([...(m.value?.versions || []).map(v => Number(v.id)), Number(route.params.id)])]
    for (const id of ids) {
      await api('/api/movies/' + id, { method: 'PATCH', body: JSON.stringify({ watched: true }) })
    }
    m.value = await api('/api/movies/' + route.params.id)
    syncForm()
    flashSaved()
  } catch (e) { /* 忽略 */ }
}

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
  heroVid.value = Number(route.params.id)
  syncForm()
  loadMedia()
  try {
    const h = await api('/api/health')
    noFfmpeg.value = !h.ffmpeg
    buildVer.value = h.build || ''
  } catch (e) { /* 健康检查失败不挡详情页 */ }
  await reloadFiles()
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
async function reloadFiles() {
  try {
    sideFiles.value = await api('/api/movies/' + route.params.id + '/files')
  } catch (e) { sideFiles.value = null }
  try {
    m.value = await api('/api/movies/' + route.params.id)
    syncForm()
  } catch (e) { /* 忽略 */ }
}

// 本片文件管理：上传（进度+取消）/下载/预览/删除（正片二次确认）
const fileGroups = computed(() => {
  const s = sideFiles.value || {}
  return [
    { key: 'extras', label: '🎬 花絮', items: s.extras || [] },
    { key: 'samples', label: '🎞 样片', items: s.samples || [] },
    { key: 'subtitles', label: '💬 字幕', items: s.subtitles || [] },
    { key: 'nfos', label: 'NFO', items: s.nfos || [] },
    { key: 'others', label: '周边（音乐/海报/剧本等）', items: s.others || [] },
  ]
})
function baseName(p) {
  return String(p || '').split('/').pop()
}
function fmtSize(n) {
  n = Number(n) || 0
  if (n < 1024) return n + 'B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'M'
  return (n / 1024 / 1024 / 1024).toFixed(2) + 'G'
}
function blobUrl(name) {
  return `/api/movies/${route.params.id}/blob?name=${encodeURIComponent(name)}`
}
const upInput = ref(null)
const upSubdir = ref('')
const upPct = ref(null)
const upMsg = ref('')
let upAbort = null
const upCtl = computed(() => !!upAbort)
async function doUpload() {
  const files = upInput.value && upInput.value.files
  if (!files || !files.length) {
    upMsg.value = '先选文件'
    return
  }
  const file = files[0]
  upMsg.value = ''
  upPct.value = 0
  const h = apiUpload(`/api/movies/${route.params.id}/upload`, file, {
    subdir: upSubdir.value,
    onProgress: (p) => { upPct.value = p }
  })
  upAbort = h.abort
  try {
    const r = await h.promise
    upPct.value = 100
    upMsg.value = `已上传 ${file.name}` + (r && r.status && r.status !== 'stored' ? `（${r.status}）` : '')
    upInput.value.value = ''
    await reloadFiles()
  } catch (e) {
    upMsg.value = '上传失败：' + e.message
  } finally {
    upAbort = null
    setTimeout(() => { if (!upAbort) upPct.value = null }, 3000)
  }
}
function cancelUpload() {
  if (upAbort) upAbort()
}
const pvName = ref('')
const pvUrl = ref('')
const pvKind = ref('')
const pvText = ref('')
const pvErr = ref(false)
function pvKindOf(name) {
  const ex = ('.' + String(name || '').split('.').pop()).toLowerCase()
  if (['.mp4', '.mkv', '.webm', '.mov', '.avi', '.ts', '.m2ts', '.flv'].includes(ex)) return 'video'
  if (['.jpg', '.jpeg', '.png', '.webp', '.gif'].includes(ex)) return 'image'
  if (ex === '.pdf') return 'pdf'
  if (['.txt', '.srt', '.ass', '.ssa', '.lrc', '.nfo', '.md'].includes(ex)) return 'text'
  return ''
}
function isVideo(name) {
  return pvKindOf(name) === 'video'
}
async function openPlayer(f) {
  const name = f.rel || f.name
  pvErr.value = false
  pvName.value = f.name
  pvUrl.value = blobUrl(name)
  pvKind.value = pvKindOf(f.name)
  pvText.value = ''
  if (pvKind.value === 'text') {
    try {
      const r = await fetch(`/api/movies/${route.params.id}/blob?name=${encodeURIComponent(name)}&mode=text`)
      pvText.value = await r.text()
    } catch (e) {
      pvText.value = '预览失败：' + e.message
    }
  }
}
function closePlayer() {
  // 弹窗 v-if 卸载 <video> 即停播
  pvName.value = ''
  pvUrl.value = ''
  pvKind.value = ''
  pvText.value = ''
  pvErr.value = false
}
const delArm = ref({})
const delMsg = ref('')
// 海报大图：先展本地 w500（即时），后台拉原图成功后替换；失败静默保持
const posterDlg = ref(false)
const posterBig = ref('')
const posterHi = ref(false)
let posterObjUrl = ''
async function openPoster() {
  if (!m.value?.poster_path) return
  posterBig.value = posterUrl(m.value.poster_path)
  posterHi.value = false
  posterDlg.value = true
  try {
    const r = await fetch(`/api/movies/${route.params.id}/poster-orig`)
    if (!r.ok) return
    const b = await r.blob()
    if (posterObjUrl) URL.revokeObjectURL(posterObjUrl)
    posterObjUrl = URL.createObjectURL(b)
    if (posterDlg.value) {
      posterBig.value = posterObjUrl
      posterHi.value = true
    }
  } catch (e) { /* 保持本地图 */ }
}
function closePoster() {
  posterDlg.value = false
  posterBig.value = ''
  posterHi.value = false
  if (posterObjUrl) {
    URL.revokeObjectURL(posterObjUrl)
    posterObjUrl = ''
  }
}
async function doFileDelete(f) {
  const name = f.rel || f.name
  delMsg.value = ''
  try {
    const d = await api('/api/movies/' + route.params.id + '/files', {
      method: 'DELETE',
      body: JSON.stringify({ name, dry_run: true })
    })
    const p = (d.plans || [])[0] || {}
    if (p.requires_confirm) {
      delArm.value[name] = p
      delMsg.value = `警告：将删除正片 ${f.name}，海报墙同步移除。再点「确认删除正片」执行`
      return
    }
    const d2 = await api('/api/movies/' + route.params.id + '/files', {
      method: 'DELETE',
      body: JSON.stringify({ name, dry_run: false })
    })
    const r = (d2.results || [])[0] || {}
    delMsg.value = r.status === 'deleted' ? '已删除' : ('删除：' + (r.status || '失败'))
    closePlayer()
    await reloadFiles()
  } catch (e) {
    delMsg.value = '删除失败：' + e.message
  }
}
async function doFileDeleteConfirm(f) {
  const name = f.rel || f.name
  try {
    const d = await api('/api/movies/' + route.params.id + '/files', {
      method: 'DELETE',
      body: JSON.stringify({ name, dry_run: false, confirm: true })
    })
    const r = (d.results || [])[0] || {}
    delMsg.value = r.status === 'deleted' ? '正片已删除，库已同步清理' : ('删除：' + (r.status || '失败'))
    delete delArm.value[name]
    closePlayer()
    await reloadFiles()
  } catch (e) {
    delMsg.value = '删除失败：' + e.message
  }
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
function escPlayer(e) {
  if (e.key !== 'Escape') return
  if (pvName.value) closePlayer()
  else if (posterDlg.value) closePoster()
}
onMounted(() => {
  load()
  window.addEventListener('keydown', escPlayer)
})
onUnmounted(() => {
  window.removeEventListener('keydown', escPlayer)
  if (preTimer) clearInterval(preTimer)
})
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
.hero-inner { position: relative; width: 100%; box-sizing: border-box; padding: 12px 24px; max-width: min(1600px, 100%); margin: 0 auto; }
.topbar { display: flex; justify-content: space-between; align-items: center; }
.top-right { display: flex; gap: 8px; align-items: center; }
.saved-flash { color: #7ed321; font-size: 0.875rem; }
.ver-tag { color: #555; font-size: 0.75rem; }
.hero-main { display: flex; gap: 20px; margin-top: 12px; align-items: flex-start; }
.poster { width: 220px; border-radius: 8px; box-shadow: 0 8px 28px rgba(0,0,0,.55); }
.poster.zoomable { cursor: zoom-in; }
.poster-big { max-height: 78vh; width: auto; max-width: 100%; margin: 0 auto; display: block; }
.poster-dlg { text-align: center; }
.poster-dlg .bar { justify-content: center; }
.poster-empty { aspect-ratio: 2/3; display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: center; background: #262626; color: #888; font-size: 0.875rem; box-shadow: none; }
.hero-info { min-width: 0; }
.hero-info h2 { margin: 0 0 8px; font-size: 1.875rem; }
.hero-info .year { color: #aaa; font-weight: normal; font-size: 1.3125rem; }
.needs-review { color: #ff6b6b; font-size: 0.875rem; border: 1px solid #6e2b2b; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }
.edition-chip { color: #6ab0ff; font-size: 0.875rem; border: 1px solid #2b4a6e; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }
.edition-chip.spec { color: #7ed321; border-color: #3a5a1e; }
.meta-line { color: #aaa; font-size: 1rem; margin: 8px 0; }
.media-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.media-badge { color: #9ecfff; font-size: 0.875rem; border: 1px solid #2b4a6e; border-radius: 999px; padding: 1px 10px; }
.media-warn { color: #e0a63c; font-size: 0.8125rem; border: 1px dashed #6e5426; border-radius: 999px; padding: 1px 10px; }
.media-loading { color: #666; font-size: 0.8125rem; }
.resume-hint { color: #7ed321; font-size: 0.8125rem; }
.play-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 12px 0 2px; }
.play-main { font-size: 1rem; padding: 8px 28px; border-radius: 999px; background: #2b6cb0; border: 1px solid #2b6cb0; color: #fff; cursor: pointer; }
.play-main:hover:not(:disabled) { background: #3580cc; }
.play-main:disabled { background: #333; border-color: #444; color: #777; cursor: not-allowed; }
.ver-sel { background: #262626; color: #ccc; border: 1px solid #444; border-radius: 8px; padding: 6px 8px; max-width: 320px; }
.pre-btn { background: transparent; border: 1px dashed #6e5426; color: #e0a63c; border-radius: 999px; padding: 6px 14px; cursor: pointer; font-size: 0.8125rem; }
.pre-btn:disabled { opacity: 0.6; cursor: wait; }
.friendly-chip { color: #7ed321; font-size: 0.8125rem; }
.trans-chip { color: #e0a63c; font-size: 0.75rem; border: 1px dashed #6e5426; border-radius: 999px; padding: 0 8px; }
.tvplay summary { cursor: pointer; color: #ccc; font-size: 0.9375rem; }
.tvplay .hint { color: #888; font-size: 0.8125rem; }
.src { color: #888; font-weight: normal; }
.tag-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.tag-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px dashed #555; color: #ccc; }
.col-chip { font-size: 0.8125rem; padding: 3px 12px; border-radius: 999px; border: 1px solid #2b4a6e; color: #6ab0ff; cursor: pointer; }
.watched-chip { color: #7ed321; font-size: 0.875rem; border: 1px solid #3a5a1e; border-radius: 999px; padding: 1px 10px; margin-left: 8px; vertical-align: middle; }
.hint-row { margin-top: 6px; color: #aaa; font-size: 0.875rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.hint-row .fhint { color: #777; font-size: 0.75rem; }
.sections { width: 100%; box-sizing: border-box; padding: 0 24px; max-width: min(1600px, 100%); display: flex; flex-direction: column; gap: 12px; margin: 12px auto 0; }
.body-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 400px); gap: 12px; align-items: start; }
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
.facts .fact { display: flex; gap: 10px; font-size: 0.875rem; margin: 8px 0; align-items: flex-start; }
.facts .fact span:first-child { color: #888; min-width: 48px; flex-shrink: 0; }
.facts .fact-val { min-width: 0; flex: 1; overflow-wrap: anywhere; word-break: break-word; line-height: 1.6; }
.facts .fact-val button { flex-shrink: 0; margin-left: 6px; white-space: nowrap; }
.facts a { color: #6ab0ff; margin-right: 10px; }
.files summary { cursor: pointer; color: #ccc; font-size: 0.9375rem; }
.files ul { color: #888; font-size: 0.875rem; }
.files a { color: #6ab0ff; margin-left: 6px; }
.up-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 0; }
.up-bar { height: 6px; background: #262626; border-radius: 3px; overflow: hidden; margin: 4px 0; }
.up-bar i { display: block; height: 100%; background: #6ab0ff; }
.f-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 3px 0; }
.f-name { flex: 1; min-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.f-size { color: #666; font-size: 0.75rem; }
.f-acts { display: flex; gap: 8px; align-items: center; margin-left: auto; }
.ver-list { list-style: none; margin: 4px 0; padding: 0; }
.q-tip { position: relative; display: inline-flex; width: 18px; height: 18px; border-radius: 50%; border: 1px solid #555; color: #aaa; font-size: 0.75rem; align-items: center; justify-content: center; cursor: help; font-weight: normal; }
.q-tip .q-bubble { display: none; position: absolute; left: 50%; top: 130%; transform: translateX(-50%); width: 280px; background: #262626; border: 1px solid #444; border-radius: 8px; padding: 10px 12px; color: #ccc; font-size: 0.8125rem; line-height: 1.7; z-index: 30; white-space: normal; }
.q-tip:hover .q-bubble, .q-tip:focus-within .q-bubble { display: block; }
.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.66); display: flex; align-items: center; justify-content: center; z-index: 50; }
.dlg { background: #1c1c1c; border-radius: 10px; padding: 16px; min-width: 320px; max-width: 860px; width: calc(100vw - 48px); max-height: 88vh; overflow: auto; }
.dlg h3 { margin: 0 0 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
button.danger { border-color: #6e2b2b; color: #ff8a8a; }
.hint.warn { color: #e0a63c; }
.pv-video { width: 100%; max-height: 480px; background: #000; border-radius: 8px; }
.pv-img { max-width: 100%; border-radius: 8px; }
.pv-pdf { width: 100%; height: 480px; border: none; border-radius: 8px; background: #fff; }
.pv-text { white-space: pre-wrap; max-height: 320px; overflow: auto; background: #111; padding: 10px; border-radius: 8px; color: #ccc; }
.edit-panel .bar { padding: 6px 0; }
</style>
