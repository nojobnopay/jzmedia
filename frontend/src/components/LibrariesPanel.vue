<template>
  <section id="sec-libraries" class="card-block">
    <div class="section-heading"><h3>媒体库列表</h3><button class="primary" :disabled="createBusy" @click="showCreate = !showCreate">{{ showCreate ? '收起新建表单' : '添加媒体库' }}</button></div>
    <HelpLink page="user-guide/libraries" label="连接本地目录或 NAS 的图解" />
    <p v-if="!mediaItems.length" class="hint">添加存储位置，再选择其中的电影或剧集目录。</p>
    <div v-else class="media-connections">
      <article v-for="m in mediaItems" :key="m.id" class="media-connection" :class="{ off: !m.enabled }">
        <header class="connection-heading">
          <button class="connection-title" :aria-expanded="!!expanded[m.id]" @click="expanded[m.id] = !expanded[m.id]">
            <AppIcon name="chevron-down" :class="{ collapsed: !expanded[m.id] }" /><strong>{{ m.name }}</strong><span v-if="m.read_only" class="badge">只读</span>
          </button>
          <span class="status-pill" :class="'st-' + statusKind(m)">{{ statusText(m) }}</span>
        </header>
        <p class="connection-path" :title="pathTitle(m)"><span>{{ m.source === 'local' ? '本地' : m.source.toUpperCase() }}</span><span v-if="driverText(m)">{{ driverText(m) }}</span><code>{{ pathText(m) }}</code></p>
        <div class="connection-meta"><span>{{ videoCounts(m) }}</span><span>{{ m.movie_count }} 片 / {{ m.episode_count }} 集</span></div>
        <p v-if="m.last_error && m.enabled" class="connection-error">{{ m.last_error }}</p>
        <div class="ops-wrap connection-actions">
          <button @click="connect(m)" :disabled="!!busy">{{ busy === 'conn' + m.id ? (m.source === 'local' ? '检查中…' : '连接中…') : (m.source === 'local' ? '检查路径' : statusKind(m) === 'ok' ? '检查连接' : '连接') }}</button>
          <button @click="scanAll(m)" :disabled="!!busy || scanning['m:' + m.id]">{{ scanning['m:' + m.id] ? '扫描中…' : '扫描此媒体库' }}</button>
          <button v-if="scanning['m:' + m.id]" @click="cancelScan('m:' + m.id)">取消</button>
          <details class="more"><summary title="更多操作">更多</summary><div class="more-menu">
            <button @click="addVideoOpen(m); closeMenu($event)">添加视频库</button>
            <button v-if="m.source !== 'local'" @click="editConn(m); closeMenu($event)">编辑连接</button>
            <button v-if="m.source === 'local' && m.movie_count + m.episode_count === 0" @click="editPath(m); closeMenu($event)">修改根目录</button>
            <button v-if="m.source !== 'local'" @click="mount(m, true); closeMenu($event)">挂载</button>
            <button v-if="m.source !== 'local'" @click="mount(m, false); closeMenu($event)">卸载</button>
            <button @click="toggleReadOnly(m); closeMenu($event)">{{ m.read_only ? '取消只读' : '设只读' }}</button>
            <button @click="toggleEnabled(m); closeMenu($event)">{{ m.enabled ? '停用' : '启用' }}</button>
            <button class="danger" @click="armDelete(m); closeMenu($event)">移除媒体库…</button>
          </div></details>
        </div>
          <div v-if="rowMsg['m:' + m.id]" class="row-msg">
            <div>
              <span :class="msgClass('m:' + m.id)">{{ rowMsg['m:' + m.id].text }}</span>
              <button v-if="rowMsg['m:' + m.id].cmd" @click="copyCmd('m:' + m.id)">复制宿主挂载命令</button>
              <button v-if="rowMsg['m:' + m.id].cmd" @click="showCmd['m:' + m.id] = !showCmd['m:' + m.id]">{{ showCmd['m:' + m.id] ? '收起命令' : '查看命令' }}</button>
              <code v-if="rowMsg['m:' + m.id].cmd && showCmd['m:' + m.id]" class="cmd-text">{{ rowMsg['m:' + m.id].cmd }}</code>
              <ul v-if="rowMsg['m:' + m.id].videos && rowMsg['m:' + m.id].videos.length" class="vid-check">
                <li v-for="v in rowMsg['m:' + m.id].videos" :key="v.id" :class="{ bad: !v.ok }">
                  {{ v.name }}（{{ v.subpath || '根' }}）{{ v.ok ? '✓' : '✗ ' + (v.error || '不可达') }}
                </li>
              </ul>
            </div>
          </div>
          <div v-if="connEdit && connEdit.id === m.id">
            <div class="path-edit">
              <label>SMB 共享路径<input v-model="connEdit.url" v-bind="NOFILL" name="jz-conn-url" placeholder="\\ServerName\ShareName\Folder" /></label>
              <label>SMB 用户名<input v-model="connEdit.username" v-bind="NOFILL" name="jz-conn-user" placeholder="SMB 登录用户名" /></label>
              <label>SMB 密码<input v-model="connEdit.password" v-bind="NOFILL_PW" name="jz-conn-pass" type="password" placeholder="SMB 密码（留空不改）" /></label>
              <label>连接地址<input v-model="connEdit.connect_host" v-bind="NOFILL" name="jz-conn-connect" placeholder="连接地址（可选，Tailscale IP）" /></label>
              <button @click="saveConn(m)" :disabled="!!busy">{{ busy === 'conn' ? '保存中…' : '保存并连接' }}</button>
              <button @click="connEdit = null">取消</button>
              <span class="fhint" :class="{ 'warn-text': connPreview.error }">{{ connPreview.error || `→ ${connPreview.host}/${connPreview.share}/${connPreview.subpath}` }}</span>
            </div>
          </div>
          <div v-if="pathEdit && pathEdit.id === m.id">
            <div class="path-edit">
              <label>媒体库根目录<input v-model="pathEdit.value" v-bind="NOFILL" name="jz-path-edit" placeholder="/media（容器内路径）" /></label>
              <button @click="savePath(m)" :disabled="!!busy">{{ busy === 'path' ? '保存中…' : '保存并检查' }}</button>
              <button @click="pathEdit = null">取消</button>
              <span class="fhint">媒体库根；仅当库内 0 记录时可改。NAS Docker 里填 /media 这类容器路径</span>
            </div>
          </div>

        <div v-if="expanded[m.id]" class="video-libraries">
          <h4>视频库 <span>{{ m.video_libraries.length }}</span></h4>
          <div v-for="v in m.video_libraries" :key="v.id" class="video-library-row" :class="{ off: !m.enabled || !v.enabled }">
            <div class="video-library-info"><div><strong>{{ v.name }}</strong><span class="badge" :class="{ tv: v.kind === 'tv' }">{{ kindText(v.kind) }}</span><span v-if="!v.enabled" class="badge">停用</span></div><p :title="v.path">{{ v.subpath || '媒体库根目录' }}</p><span class="fhint">{{ v.movie_count }} 片 / {{ v.episode_count }} 集</span><p v-if="rowMsg['v:' + v.id]" :class="msgClass('v:' + v.id)" role="status">{{ rowMsg['v:' + v.id].text }}</p></div>
            <div class="ops-wrap">
              <button @click="scanVideo(v)" :disabled="!!busy || scanning['v:' + v.id]">{{ scanning['v:' + v.id] ? '扫描中…' : '扫描' }}</button>
              <button v-if="scanning['v:' + v.id]" @click="cancelScan('v:' + v.id)">取消</button>
              <button @click="editVideo(v, m)">编辑</button>
              <button class="danger" @click="armDeleteVideo(v, m)">移除视频库…</button>
            </div>
          </div>
          <div v-if="videoEdit && videoEdit.mediaId === m.id" class="path-edit">
            <VideoLibraryForm :library="videoEdit" :disabled="!!busy" @saved="onVideoSaved" @cancel="videoEdit = null" />
          </div>
          <div v-if="newVideo[m.id]" class="path-edit">
            <label>视频库名称<input v-model="newVideo[m.id].name" v-bind="NOFILL" name="jz-nv-name" placeholder="留空按子目录命名" /></label>
            <label>子目录<input v-model="newVideo[m.id].subpath" v-bind="NOFILL" name="jz-nv-sub" placeholder="相对媒体库根目录，留空使用根目录" /></label>
            <label>内容类型<select v-model="newVideo[m.id].kind"><option value="movie">电影</option><option value="tv">剧集</option></select></label>
            <div class="ops-wrap"><button @click="createVideo(m)" :disabled="!!busy">{{ busy === 'video' ? '创建中…' : '创建视频库' }}</button><button @click="newVideo[m.id] = null">取消</button><button @click="detectSubdirs(m)" :disabled="subdirLoading[m.id]">{{ subdirLoading[m.id] ? '读取中…' : '检测子目录' }}</button></div>
            <div v-if="subdirs[m.id] && subdirs[m.id].length" class="subdir-chips"><button v-for="d in subdirs[m.id]" :key="d.rel" class="chip" @click="pickSubdir(m, d)">{{ d.rel }}</button></div>
            <span v-else-if="subdirs[m.id]" class="fhint">没有可用子目录</span>
          </div>
        </div>
      </article>
    </div>

    <p v-if="msg" class="feedback" role="status">{{ msg }}</p>
    <div v-if="created" class="created-bar">
      <span class="cb-title">媒体库「{{ created.name }}」已创建 · 下一步</span>
      <span>① <button @click="connect(created)">{{ created.source === 'local' ? '检查路径' : '连接' }}</button></span>
      <span>② <button @click="scanAll(created)">扫描全部视频库</button></span>
      <button class="cb-close" @click="created = null">关闭引导</button>
    </div>

    <div v-if="arm" class="danger-box">
      <p>将删除媒体库「{{ arm.name }}」及其 <b>{{ arm.video_libraries.length }}</b> 个视频库的
        <b>{{ arm.movie_count }}</b> 条影片记录 / <b>{{ arm.episode_count }}</b> 条剧集记录与合集/花絮/缓存索引；
        <b>磁盘文件与远端数据不会被删除</b>（Plex/文件浏览不受影响）。</p>
      <div class="bar">
        <button class="danger" @click="doDeleteMedia" :disabled="!!busy">{{ busy === 'delete' ? '删除中…' : '确认删除记录' }}</button>
        <button @click="arm = null">取消</button>
      </div>
    </div>

    <div v-if="armVideo" class="danger-box">
      <p>将删除视频库「{{ armVideo.name }}」（{{ armVideo.subpath || '媒体库根' }}）的
        <b>{{ armVideo.movie_count }}</b> 条影片 / <b>{{ armVideo.episode_count }}</b> 条剧集记录；
        <b>磁盘文件不会被删除</b>。媒体库连接保留。</p>
      <div class="bar">
        <button class="danger" @click="doDeleteVideo" :disabled="!!busy">{{ busy === 'delete' ? '删除中…' : '确认删除记录' }}</button>
        <button @click="armVideo = null">取消</button>
      </div>
    </div>

    <MediaLibraryCreateForm v-if="showCreate || !mediaItems.length" :smb-driver="smbDriver"
      :disabled="!!busy" @created="onCreated" @busy="createBusy = $event" />

  </section>
</template>

<script setup>
import HelpLink from './HelpLink.vue'
import AppIcon from './AppIcon.vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import MediaLibraryCreateForm from './MediaLibraryCreateForm.vue'
import VideoLibraryForm from './VideoLibraryForm.vue'
import { api } from '../api.js'
import { loadLibs } from '../libraries.js'
import { parseSmbInput, smbUrlOf } from '../smb.js'

const emit = defineEmits(['changed'])
const mediaItems = ref([])
const busy = ref('')
const msg = ref('')
const smbDriver = ref('auto')       // 服务端 SMB_DRIVER：auto|direct|mount
const showCreate = ref(false)
const createBusy = ref(false)
const expanded = reactive({})       // media_id -> 展开视频库
const arm = ref(null)
const armVideo = ref(null)
const pathEdit = ref(null)
const connEdit = ref(null)
const videoEdit = ref(null)
const newVideo = reactive({})       // media_id -> 新视频库表单或 null
const subdirs = reactive({})        // media_id -> [{name, rel}]
const subdirLoading = reactive({})
const created = ref(null)
const rowMsg = reactive({})     // key -> {text, kind: info|ok|error|warn, cmd?, videos?}
const showCmd = reactive({})
const scanJobs = reactive({})   // key（m:id / v:id）-> {jobId, label}
const scanning = computed(() => {
  const out = {}
  for (const k of Object.keys(scanJobs)) out[k] = true
  return out
})
const connPreview = computed(() => parseSmbInput(connEdit.value ? connEdit.value.url : ''))

const NOFILL = { autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': '', 'data-bwignore': 'true' }
const NOFILL_PW = { ...NOFILL, autocomplete: 'new-password' }

function setMsg (key, text, kind = 'info', cmd = '', videos = null) {
  rowMsg[key] = { text, kind, cmd, videos }
  if (!cmd) delete showCmd[key]
}
function msgClass (key) {
  const k = rowMsg[key]?.kind
  return { 'msg-err': k === 'error', 'msg-ok': k === 'ok', 'msg-warn': k === 'warn' }
}

const STATUS_TEXT = {
  auth_failed: '认证失败',
  hostname_resolve_failed: '主机名无法解析',
  tcp_timeout: '连接超时',
  connection_refused: '连接被拒',
  tcp_unreachable: '网络不可达',
  smb_negotiate_failed: 'SMB 协商失败',
  share_not_found: '共享不存在',
  share_access_denied: '共享无权限',
  path_not_found: '目录不存在',
  read_failed: '读取失败',
  write_failed: '写入失败',
  stream_failed: '随机读失败',
  ffprobe_failed: '探测失败',
  ffprobe_missing: '缺少 ffprobe',
  unreachable: '连接失败',
  error: '不可读',
  library_offline: '已离线',
}

function statusKind (m) {
  if (!m.enabled) return 'off'
  if (m.last_status === 'ok') return 'ok'
  if (m.last_status === 'not_mounted' || !m.last_status) return 'idle'
  return 'err'
}

function statusText (m) {
  if (!m.enabled) return '已停用'
  if (m.last_status === 'ok') return '可读'
  if (m.last_status === 'not_mounted') return '未连接'
  return STATUS_TEXT[m.last_status] || '未检查'
}

function kindText (kind) {
  return kind === 'tv' ? '剧集' : '电影'
}

function videoCounts (m) {
  const vids = m.video_libraries || []
  const movies = vids.filter((v) => v.kind !== 'tv').length
  const tvs = vids.filter((v) => v.kind === 'tv').length
  return `${vids.length} 个（电影 ${movies} / 剧集 ${tvs}）`
}

async function load () {
  try {
    const d = await api('/api/media-libraries')
    mediaItems.value = d.items || []
    smbDriver.value = d.smb_driver || 'auto'
    // 默认展开最近使用的媒体库
    const cur = mediaItems.value[0]
    if (cur && expanded[cur.id] === undefined) expanded[cur.id] = true
    try { await loadLibs(api, { force: true }) } catch (e) { /* 忽略 */ }
    emit('changed')
  } catch (e) { msg.value = '媒体库加载失败：' + e.message }
}

function pathText (m) {
  if (m.source === 'smb') return smbUrlOf(m) || m.path
  if (m.source === 'nfs') return m.nfs_export || m.path
  return m.path
}

function pathTitle (m) {
  if (m.source === 'local') return m.path
  const lines = [pathText(m)]
  if (m.source === 'smb') {
    lines.push(`访问方式：${m.driver === 'mount' ? '容器/宿主挂载' : '用户态直读（无需挂载）'}`)
    if (m.smb_connect_host && m.smb_connect_host !== m.smb_host) {
      lines.push(`实际连接地址：${m.smb_connect_host}`)
    }
  } else {
    lines.push('访问方式：宿主挂载')
  }
  lines.push(`宿主挂载回退路径：${m.path}`)
  return lines.join('\n')
}

function driverText (m) {
  if (m.source === 'smb') return m.driver === 'mount' ? '挂载' : '直读'
  if (m.source === 'nfs') return '挂载'
  return ''
}

function closeMenu (e) {
  const d = e.target.closest('details')
  if (d) d.removeAttribute('open')
}

async function copyCmd (key) {
  const cmd = rowMsg[key]?.cmd || ''
  try {
    await navigator.clipboard.writeText(cmd)
    setMsg(key, '宿主挂载命令已复制，到 NAS 终端执行后点「检查」', 'ok', cmd)
  } catch (e) {
    setMsg(key, '复制失败，请手动选择命令文本复制', 'warn', cmd)
  }
}

// 主操作：本地=检查；远程=挂载 + 检查（挂载模式）或直读诊断（direct/auto）
async function connect (m) {
  busy.value = 'conn' + m.id
  const direct = m.source === 'smb' && smbDriver.value !== 'mount'
  setMsg('m:' + m.id, m.source === 'local' ? '正在检查路径…'
    : direct ? '正在检测直读连接（11 阶段，最多约 1 分钟）…'
      : '正在连接（挂载 + 检查，最多约 20 秒）…')
  try {
    if (m.source !== 'local' && !direct) {
      const r = await api(`/api/media-libraries/${m.id}/mount`, { method: 'POST' })
      if (!r.ok) {
        const cmd = r.suggested_cmd || ''
        setMsg('m:' + m.id, `连接失败：${r.error || '未知原因'}`
          + (cmd ? '（容器无挂载权限，可用下方宿主命令挂载）' : ''), 'error', cmd)
        await load()
        return
      }
    }
    const d = await api(`/api/media-libraries/${m.id}/check`, { method: 'POST' })
    const bad = (d.video_libraries || []).filter((v) => !v.ok)
    if (d.readable) {
      const head = `连接正常：${d.writable ? '可读可写' : '可读（只读）'}`
        + (direct ? '，直读模式（免挂载）' : '') + '，可以扫描入库了'
      if (bad.length) {
        setMsg('m:' + m.id, `${head}，但 ${bad.length} 个视频库子目录不可达（见下）`, 'warn', '', bad)
      } else if (d.warning) {
        setMsg('m:' + m.id, `${head}。注意：${d.warning}`
          + '（归档/NFO 写入将跳过，可把该库勾选为「只读库」）', 'warn')
      } else {
        setMsg('m:' + m.id, head, 'ok', '', (d.video_libraries || []))
      }
    } else {
      const sug = (d.suggestions || []).slice(0, 2).join('；')
      const why = d.message || d.reason || d.error || '请检查路径/权限'
      setMsg('m:' + m.id, `检测失败${d.stage ? '（' + d.stage + '）' : ''}：${why}`
        + (sug ? '。建议：' + sug : ''), 'error',
      direct ? '' : (d.suggested_cmd || ''))
    }
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '连接失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

let scanTimer = null
function stopScanTimer () {
  if (scanTimer) { clearInterval(scanTimer); scanTimer = null }
}
function ensureScanTimer () {
  if (!scanTimer) scanTimer = setInterval(pollScans, 1200)
}

async function _scanStart (key, body, resumedCheck) {
  try {
    const d = await api('/api/jobs/scan', { method: 'POST', body: JSON.stringify(body) })
    if (d.resumed && resumedCheck(d)) {
      setMsg(key, '已有其他扫描在跑，完成后请再试', 'warn')
      return
    }
    scanJobs[key] = { jobId: d.job_id }
    if (d.resumed) setMsg(key, '已有扫描在跑，正在跟踪进度…')
    ensureScanTimer()
  } catch (e) {
    setMsg(key, '扫描启动失败：' + e.message, 'error')
  }
}

async function scanAll (m) {
  setMsg('m:' + m.id, '正在启动扫描（全部视频库）…')
  await _scanStart('m:' + m.id, { media_library_id: m.id },
    (d) => d.media_library_id == null || Number(d.media_library_id) !== Number(m.id))
}

async function scanVideo (v) {
  setMsg('v:' + v.id, '正在启动扫描…')
  await _scanStart('v:' + v.id, { library_id: v.id },
    (d) => d.library_id == null || Number(d.library_id) !== Number(v.id))
}

async function cancelScan (key) {
  const job = scanJobs[key]
  if (!job) return
  try { await api('/api/jobs/scan/' + job.jobId + '/cancel', { method: 'POST' }) } catch (e) { /* 下次轮询收尾 */ }
}

async function pollScans () {
  const keys = Object.keys(scanJobs)
  if (!keys.length) { stopScanTimer(); return }
  for (const key of keys) {
    try {
      const st = await api('/api/jobs/scan/' + scanJobs[key].jobId)
      if (st.state === 'running') {
        setMsg(key, st.total ? `扫描中 ${st.done || 0}/${st.total}…` : '扫描中…')
        continue
      }
      const c = (st.summary || {}).counts || {}
      if (st.state === 'done') {
        const ok = (c.ok || 0) + (c.ok_needs_review || 0)
        setMsg(key, `扫描完成：新增/更新 ${ok}，未匹配 ${c.no_match || 0}`
          + (c.scan_failed ? `，刮削失败 ${c.scan_failed}（可在详情页重试）` : '')
          + (c.library_offline ? '；有库离线已跳过' : '')
          + '；未匹配片可到「入库流程 → 待匹配确认」处理', 'ok')
      } else if (st.state === 'cancelled') {
        setMsg(key, `已取消（${st.done || 0}/${st.total || 0}）`, 'warn')
      } else {
        setMsg(key, '扫描失败：' + (st.error || '未知错误'), 'error')
      }
      delete scanJobs[key]
    } catch (e) { /* 轮询失败下次继续 */ }
  }
  if (!Object.keys(scanJobs).length) { stopScanTimer(); await load() }
}

function editPath (m) {
  pathEdit.value = { id: m.id, value: m.path || '' }
}

async function savePath (m) {
  const v = (pathEdit.value?.value || '').trim()
  if (!v) return
  busy.value = 'path'
  try {
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ path: v }),
    })
    pathEdit.value = null
    await connect({ id: m.id, name: m.name, source: m.source })
  } catch (e) {
    setMsg('m:' + m.id, '改路径失败：' + e.message, 'error')
    busy.value = ''
  }
}

function editConn (m) {
  connEdit.value = { id: m.id, url: smbUrlOf(m), username: m.smb_username || '',
                     password: '', connect_host: m.smb_connect_host || '' }
}

async function saveConn (m) {
  const p = connPreview.value
  if (p.error) { setMsg('m:' + m.id, p.error, 'error'); return }
  busy.value = 'conn'
  try {
    const smb = { username: connEdit.value.username,
                  connect_host: connEdit.value.connect_host || '',
                  domain: m.smb_domain || '', options: m.smb_options || '' }
    if (connEdit.value.password) smb.password = connEdit.value.password
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ smb_url: connEdit.value.url, smb }),
    })
    connEdit.value = null
    setMsg('m:' + m.id, '连接信息已保存，正在重新连接…', 'ok')
    await connect({ id: m.id, name: m.name, source: m.source })
  } catch (e) {
    setMsg('m:' + m.id, '保存连接失败：' + e.message, 'error')
    busy.value = ''
  }
}

async function toggleReadOnly (m) {
  busy.value = 'patch' + m.id
  try {
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ read_only: !m.read_only }),
    })
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '保存失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function toggleEnabled (m) {
  busy.value = 'patch' + m.id
  try {
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ enabled: !m.enabled }),
    })
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '保存失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function armDelete (m) {
  arm.value = m
  armVideo.value = null
}

async function doDeleteMedia () {
  if (!arm.value) return
  busy.value = 'delete'
  try {
    const d = await api(`/api/media-libraries/${arm.value.id}`, { method: 'DELETE' })
    msg.value = `已移除媒体库「${d.library}」：影片 ${d.movies} / 剧集 ${d.tv_episodes} / 合集 ${d.collections}（文件保留）`
    delete rowMsg['m:' + arm.value.id]
    arm.value = null
    await load()
  } catch (e) {
    msg.value = '删除失败：' + e.message
  } finally {
    busy.value = ''
  }
}

function armDeleteVideo (v, m) {
  arm.value = null
  armVideo.value = { id: v.id, name: v.name, subpath: v.subpath,
    movie_count: v.movie_count, episode_count: v.episode_count, mediaId: m.id }
}

async function doDeleteVideo () {
  if (!armVideo.value) return
  busy.value = 'delete'
  try {
    const d = await api(`/api/libraries/${armVideo.value.id}`, { method: 'DELETE' })
    msg.value = `已移除视频库「${d.library}」：影片 ${d.movies}（文件保留）`
    delete rowMsg['v:' + armVideo.value.id]
    armVideo.value = null
    await load()
  } catch (e) {
    msg.value = '删除失败：' + e.message
  } finally {
    busy.value = ''
  }
}

async function mount (m, up) {
  busy.value = (up ? 'mount' : 'unmount') + m.id
  try {
    const d = await api(`/api/media-libraries/${m.id}/${up ? 'mount' : 'unmount'}`,
                        { method: 'POST' })
    if (d.ok) {
      setMsg('m:' + m.id, up ? '已挂载，可点「检查」确认可读' : '已卸载', 'ok')
    } else {
      setMsg('m:' + m.id, `挂载失败：${d.error || '未知原因'}`, 'error', d.suggested_cmd || '')
    }
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '挂载操作失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function addVideoOpen (m) {
  expanded[m.id] = true
  subdirs[m.id] = null
  newVideo[m.id] = { name: '', subpath: '', kind: 'movie' }
}

async function detectSubdirs (m) {
  subdirLoading[m.id] = true
  try {
    const d = await api(`/api/media-libraries/${m.id}/subdirs`)
    subdirs[m.id] = d.dirs || []
  } catch (e) {
    subdirs[m.id] = []
    setMsg('m:' + m.id, '检测子目录失败：' + e.message, 'error')
  } finally {
    subdirLoading[m.id] = false
  }
}

function pickSubdir (m, d) {
  if (!newVideo[m.id]) return
  newVideo[m.id].subpath = d.rel
  if (!newVideo[m.id].name) newVideo[m.id].name = d.name
}

async function createVideo (m) {
  const v = newVideo[m.id]
  if (!v) return
  busy.value = 'video'
  try {
    const d = await api('/api/libraries', {
      method: 'POST',
      body: JSON.stringify({ media_library_id: m.id, name: v.name, subpath: v.subpath,
                             kind: v.kind }),
    })
    msg.value = `已创建视频库「${d.name}」`
    newVideo[m.id] = null
    subdirs[m.id] = null
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '创建视频库失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function editVideo (v, m) {
  videoEdit.value = { id: v.id, mediaId: m.id, name: v.name, subpath: v.subpath,
                      kind: v.kind, movie_count: v.movie_count,
                      episode_count: v.episode_count }
}

async function onCreated(d) {
  showCreate.value = false
  created.value = d
  expanded[d.id] = true
  await load()
  if (d.source !== 'local') await connect(d)
}
async function onVideoSaved() { videoEdit.value = null; await load() }

onMounted(load)
onBeforeUnmount(stopScanTimer)
defineExpose({ ensure: load })
</script>

<style scoped>
.media-connections { margin-top: 24px; }
.media-connection { padding: 24px 0; border-top: 1px solid var(--jz-border); }
.media-connection:first-child { padding-top: 0; border-top: 0; }
.off { opacity: .55; }
.connection-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.connection-title { display: inline-flex; align-items: center; gap: 10px; min-width: 0; padding: 0; background: none; border: 0; text-align: left; font-size: var(--jz-font-xl); }
.connection-title strong { overflow-wrap: anywhere; }
.connection-title svg { flex-shrink: 0; width: 18px; }
.connection-title .collapsed { transform: rotate(-90deg); }
.connection-path { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; margin: 10px 0 8px 28px; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.connection-path code { font-family: inherit; font-size: var(--jz-font-m); overflow-wrap: anywhere; color: var(--jz-text); }
.connection-meta { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-left: 28px; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.connection-error { color: var(--jz-danger); font-size: var(--jz-font-s); overflow-wrap: anywhere; }
.ops-wrap { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.connection-actions { margin: 18px 0 0 28px; }
.badge { display: inline-block; margin-left: 8px; font-size: var(--jz-font-s); color: var(--jz-text-dim); border: 1px solid var(--jz-border-strong); border-radius: 4px; padding: 1px 6px; font-weight: 400; }
.danger { border-color: var(--jz-danger-border); color: var(--jz-danger); }
.danger-box { border: 1px solid var(--jz-danger-border); background: var(--jz-danger-soft); border-radius: var(--jz-radius-m); padding: 16px; margin: 16px 0; }
.status-pill { flex-shrink: 0; display: inline-flex; align-items: center; gap: 6px; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.status-pill::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.status-pill.st-ok, .msg-ok { color: var(--jz-success); }
.status-pill.st-err, .msg-err { color: var(--jz-danger); }
.status-pill.st-idle, .msg-warn { color: var(--jz-warn); }
.row-msg { background: var(--jz-surface-2); padding: 12px 16px; margin-top: 16px; font-size: var(--jz-font-m); }
.row-msg button { margin-left: 8px; }
.cmd-text { display: block; margin-top: 8px; padding: 8px; background: var(--jz-bg); color: var(--jz-link); white-space: pre-wrap; overflow-wrap: anywhere; }
.vid-check { margin: 8px 0 0; padding-left: 20px; color: var(--jz-success); }
.vid-check li.bad { color: var(--jz-danger); }
.video-libraries { margin: 20px 0 0 28px; padding: 16px 20px; background: var(--jz-surface); border-radius: var(--jz-radius-m); }
.video-libraries h4 { margin: 0 0 8px; color: var(--jz-text-dim); font-size: var(--jz-font-s); font-weight: 500; }
.video-libraries h4 span { margin-left: 6px; }
.video-library-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 16px 0; font-size: var(--jz-font-m); }
.video-library-row + .video-library-row { border-top: 1px solid var(--jz-border); }
.video-library-info { min-width: 0; }
.video-library-info p { margin: 6px 0; color: var(--jz-text-dim); overflow-wrap: anywhere; }
.more { position: relative; display: inline-block; }
.more > summary { list-style: none; cursor: pointer; padding: 6px 10px; border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-s); font-size: var(--jz-font-m); }
.more > summary::-webkit-details-marker { display: none; }
.more[open] > summary { border-color: var(--jz-text-dim); }
.more-menu { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 0 0; }
.more-menu button { text-align: left; }
.created-bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin: 16px 0; padding: 14px 16px; border: 1px solid var(--jz-info-border); background: var(--jz-info-soft); border-radius: var(--jz-radius-m); font-size: var(--jz-font-m); }
.created-bar .cb-title { color: var(--jz-link); font-weight: 600; }
.created-bar .cb-close { margin-left: auto; }
.fhint { color: var(--jz-text-dim); font-size: var(--jz-font-s); font-weight: normal; }
.hint { color: var(--jz-text-dim); font-size: var(--jz-font-m); line-height: 1.6; }
.path-edit { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; margin: 16px 0; padding-top: 16px; border-top: 1px solid var(--jz-border); }
.path-edit label { display: flex; flex-direction: column; gap: 8px; flex: 1 1 220px; font-size: var(--jz-font-m); }
.path-edit input { width: 100%; min-width: 0; box-sizing: border-box; }
.path-edit .ops-wrap { flex-basis: 100%; }
.subdir-chips { flex-basis: 100%; display: flex; gap: 8px; flex-wrap: wrap; }
@media (max-width: 700px) {
  .media-connections { margin-top: 20px; }
  .connection-actions, .connection-path, .connection-meta { margin-left: 0; }
  .video-libraries { margin-left: 0; padding: 14px; }
  .video-library-row { display: block; }
  .video-library-row .ops-wrap { margin-top: 12px; }
  .path-edit { align-items: stretch; }
  .path-edit > button, .more > summary { min-height: 44px; }
}
</style>
