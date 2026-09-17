<template>
      <section id="sec-files" class="card-block">
        <h3>文件浏览</h3>
        <p class="hint">类 Windows 操作：单击选中（Ctrl 多选）、双击进入目录/正片进详情；快捷键
          Ctrl+C 复制 / Ctrl+X 剪切 / Ctrl+V 粘贴到当前目录 / Delete 删除 / Enter 进入 / Backspace 上级 / F5 刷新。
          复制冲突自动改「(副本)」绝不覆盖；正片复制会登记为同片新版本；目录不支持改名（请用「归档整理」）。</p>
        <div class="bar fs-crumbs">
          <button @click="loadFs('')" :disabled="!!busy">根</button>
          <span v-for="c in fsCrumbs" :key="c.rel"> / <button @click="loadFs(c.rel)" :disabled="!!busy" class="linklike">{{ c.name }}</button></span>
          <span v-if="fsPath" class="miss-path">{{ fsPath }}</span>
        </div>
        <div class="bar">
          <input v-model="fsMkdirName" placeholder="新子目录名" style="width:160px" />
          <button @click="doFsMkdir" :disabled="!!busy || !fsMkdirName.trim()">新建目录</button>
          <button @click="copySelection" :disabled="!!busy || !selRels.length">复制</button>
          <button @click="cutSelection" :disabled="!!busy || !selRels.length">剪切</button>
          <button @click="paste" :disabled="!!busy || !clipboard.rels.length || copyJob">
            {{ clipboard.mode === 'cut' ? '粘贴（移动）' : '粘贴' }}
          </button>
          <button @click="deleteSelection" :disabled="!!busy || !selRels.length">删除选中</button>
          <span v-if="selRels.length" class="fhint">已选 {{ selRels.length }} 项</span>
          <span v-else-if="clipboard.rels.length" class="fhint">
            剪贴板：{{ clipboard.mode === 'cut' ? '已剪切' : '已复制' }} {{ clipboard.rels.length }} 项
          </span>
        </div>
        <div v-if="fsParent !== null" class="bar">
          <button @click="loadFs(fsParent)" :disabled="!!busy">‹ 上级目录</button>
          <span>{{ fsMsg }}</span>
        </div>
        <div v-if="pendCopy" class="bar fs-prompt">
          <span class="warn-text">复制确认：{{ pendCopy.hint }}。继续？</span>
          <button @click="confirmCopy" :disabled="!!busy">确认复制</button>
          <button @click="pendCopy = null">取消</button>
        </div>
        <div v-if="copyJob" class="up-progress"><div class="up-progress-fill" :style="{ width: copyPct + '%' }"></div></div>
        <div v-if="copyJob" class="bar">
          <span class="fhint">复制中 {{ copyJob.done }}/{{ copyJob.total }}（{{ fmtBytes(copyJob.bytesDone || 0) }}/{{ fmtBytes(copyJob.bytesTotal || 0) }}）</span>
          <button @click="cancelCopy">取消复制</button>
        </div>
        <div v-for="(p, i) in fsPrompts" :key="'p' + i" class="bar fs-prompt">
          <span class="warn-text">{{ p.text }}</span>
          <button v-if="p.movieId" @click="router.push('/m/' + p.movieId)">{{ p.label || '去详情匹配' }}</button>
          <button v-else-if="p.kind === 'scan'" @click="emit('scan')">立即扫描新文件</button>
          <button @click="fsPrompts = fsPrompts.filter((_, j) => j !== i)">知道了</button>
        </div>
        <ul v-if="fsDirs.length" class="miss-list">
          <li v-for="d in fsDirs" :key="'d' + d.rel"
            :class="['miss-row', 'fs-row', { sel: selRels.includes(d.rel) }]"
            @click="onRowClick($event, d.rel)" @dblclick="enterDir(d.rel)">
            <span class="miss-title">📁 {{ d.name }}</span>
            <span class="miss-path">{{ d.children }} 项</span>
            <button @click.stop="enterDir(d.rel)" :disabled="!!busy">进入</button>
            <button @click.stop="doFsDelete(d.rel)" :disabled="!!busy">删空目录</button>
          </li>
        </ul>
        <ul v-if="fsFiles.length" class="miss-list">
          <li v-for="f in fsFiles" :key="'f' + f.rel"
            :class="['miss-row', 'fs-file-row', 'fs-row', { sel: selRels.includes(f.rel) }]"
            @click="onRowClick($event, f.rel)" @dblclick="openFile(f)">
            <span v-if="f.kind === 'feature'" class="kind-badge bad">正片</span>
            <span v-else-if="f.kind === 'sidecar'" class="kind-badge">花絮</span>
            <span v-else-if="f.kind === 'subtitle'" class="kind-badge">字幕</span>
            <span v-else-if="f.kind === 'nfo'" class="kind-badge">NFO</span>
            <span v-else class="kind-badge">其他</span>
            <span class="miss-title">{{ f.name }}</span>
            <span class="miss-path">{{ fmtBytes(f.size) }}{{ f.title ? ` · ${f.title}` : '' }}</span>
            <input v-model="fsRenameEdits[f.rel]" placeholder="新文件名" style="width:140px" @click.stop />
            <button @click.stop="doFsRename(f.rel)" :disabled="!!busy">{{ fsArmAction('rename', f.rel) ? '确认改名' : '改名' }}</button>
            <button v-if="!fsArmDelete[f.rel]" @click.stop="doFsDelete(f.rel)" :disabled="!!busy">删除</button>
            <button v-else @click.stop="doFsDeleteConfirm(f.rel)" :disabled="!!busy" class="danger">确认删除正片</button>
          </li>
        </ul>
        <p v-if="fsArmHint" class="hint warn-text">{{ fsArmHint }}</p>
        <p v-if="!fsDirs.length && !fsFiles.length" class="hint">空目录</p>
      </section>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'
import { usePolling } from '../usePolling.js'

// 文件浏览（评审 R14-Q2 + B9 后续）：从 Settings.vue 抽出；Windows 式选中/双击/快捷键/复制粘贴。
// 删除正片/复制正片登记后发 changed 让父页刷新统计；目录复制完成引导父页触发扫描。
const props = defineProps({ active: { type: Boolean, default: false } })
const emit = defineEmits(['changed', 'scan'])
const router = useRouter()
const busy = ref(null)

const fsPath = ref('')
const fsParent = ref('')
const fsCrumbs = ref([])
const fsDirs = ref([])
const fsFiles = ref([])
const fsMsg = ref('')
const fsMkdirName = ref('')
const fsRenameEdits = ref({})
const fsArmDelete = ref({})
const fsArmHint = ref('')
const selRels = ref([])
const clipboard = ref({ mode: 'copy', rels: [] })
const pendCopy = ref(null)
const fsPrompts = ref([])
const copyJob = ref(null)

const copyPct = computed(() => {
  const j = copyJob.value
  if (!j) return 0
  if (j.bytesTotal) return Math.min(100, Math.round((j.bytesDone || 0) * 100 / j.bytesTotal))
  return j.total ? Math.min(100, Math.round((j.done || 0) * 100 / j.total)) : 0
})

function fsKindText(k) {
  return { feature: '正片', sidecar: '花絮', subtitle: '字幕', nfo: 'NFO', other: '其他', dir: '目录' }[k] || k
}

async function loadFs(path) {
  fsMsg.value = ''
  fsArmHint.value = ''
  fsArmDelete.value = {}
  selRels.value = []
  try {
    const d = await api('/api/fs/list?path=' + encodeURIComponent(path || ''))
    fsPath.value = d.path || ''
    fsParent.value = d.parent ?? ''
    fsCrumbs.value = d.crumbs || []
    fsDirs.value = d.dirs || []
    fsFiles.value = d.files || []
    for (const f of fsFiles.value) {
      if (!(f.rel in fsRenameEdits.value)) fsRenameEdits.value[f.rel] = ''
    }
  } catch (e) {
    fsMsg.value = '加载失败：' + e.message
  }
}

async function doFsMkdir() {
  const name = fsMkdirName.value.trim()
  if (!name) return
  busy.value = 'fs'
  try {
    await api('/api/fs/mkdir', { method: 'POST', body: JSON.stringify({ path: fsPath.value, name }) })
    fsMkdirName.value = ''
    fsMsg.value = `已在当前目录创建「${name}」`
    await loadFs(fsPath.value)
  } catch (e) {
    fsMsg.value = '创建失败：' + e.message
  } finally {
    busy.value = null
  }
}

function fsArmAction(kind, rel) {
  return fsArmDelete.value[kind + ':' + rel] || null
}

async function doFsRename(rel) {
  const name = (fsRenameEdits.value[rel] || '').trim()
  if (!name) {
    fsMsg.value = '先填新文件名'
    return
  }
  const armedKey = 'rename:' + rel
  if (!fsArmAction('rename', rel)) {
    // 两步确认（评审 B8/R14-D2：与删除一致，先预览再执行）
    busy.value = 'fs'
    try {
      const d = await api('/api/fs/rename', { method: 'POST', body: JSON.stringify({ from: rel, name, dry_run: true }) })
      const p = (d.plans || [])[0] || {}
      fsArmDelete.value = { ...fsArmDelete.value, [armedKey]: p }
      fsArmHint.value = p.status === 'conflict_disk_exists'
        ? `目标已存在：${p.to || name}`
        : `将改名：${rel} → ${p.to || name}。再点「确认改名」执行`
    } catch (e) {
      fsMsg.value = '改名预览失败：' + e.message
    } finally {
      busy.value = null
    }
    return
  }
  delete fsArmDelete.value[armedKey]
  busy.value = 'fs'
  try {
    const d = await api('/api/fs/rename', { method: 'POST', body: JSON.stringify({ from: rel, name, dry_run: false }) })
    const r = (d.results || [])[0] || {}
    fsMsg.value = r.status === 'moved' ? `已改名${r.followed ? `（跟随 ${r.followed} 个）` : ''}` : ('改名：' + (r.status || '失败'))
    await loadFs(fsPath.value)
    emit('changed')
  } catch (e) {
    fsMsg.value = '改名失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doFsDelete(rel) {
  busy.value = 'fs'
  fsArmHint.value = ''
  try {
    const d = await api('/api/fs/delete', { method: 'POST', body: JSON.stringify({ paths: [rel], dry_run: true }) })
    const p = (d.plans || [])[0] || {}
    if (p.status === 'dir_not_empty') {
      fsMsg.value = '目录非空，先清空再删（不做递归删）'
      return
    }
    if (p.requires_confirm) {
      fsArmDelete.value[rel] = p
      const vers = p.version_count > 1 ? `（共 ${p.version_count} 个版本中的 1 个）` : ''
      fsArmHint.value = `警告：将删除正片《${p.title || p.name}》${vers}，海报墙同步移除，关联与索引清理。再点「确认删除正片」执行`
      return
    }
    const d2 = await api('/api/fs/delete', { method: 'POST', body: JSON.stringify({ paths: [rel], dry_run: false }) })
    const r = (d2.results || [])[0] || {}
    fsMsg.value = r.status === 'deleted' ? `已物理删除（${fsKindText(p.kind)}，不可恢复）` : ('删除：' + (r.status || '失败'))
    await loadFs(fsPath.value)
    emit('changed')
  } catch (e) {
    fsMsg.value = '删除失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doFsDeleteConfirm(rel) {
  busy.value = 'fs'
  try {
    const d = await api('/api/fs/delete', { method: 'POST', body: JSON.stringify({ paths: [rel], dry_run: false, confirm: true }) })
    const r = (d.results || [])[0] || {}
    fsMsg.value = r.status === 'deleted' ? '正片已删除，库已同步清理（不可恢复）' : ('删除：' + (r.status || '失败'))
    delete fsArmDelete.value[rel]
    fsArmHint.value = ''
    await loadFs(fsPath.value)
    emit('changed')
  } catch (e) {
    fsMsg.value = '删除失败：' + e.message
  } finally {
    busy.value = null
  }
}

// ---------- 选中 / 双击 / 快捷键 ----------

function isTextTarget(e) {
  const t = e && e.target
  if (!t) return false
  const tag = (t.tagName || '').toUpperCase()
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable
}
function onRowClick(e, rel) {
  if (e.ctrlKey || e.metaKey) {
    const i = selRels.value.indexOf(rel)
    if (i >= 0) selRels.value.splice(i, 1)
    else selRels.value.push(rel)
  } else {
    selRels.value = [rel]
  }
}
function enterDir(rel) { if (!busy.value) loadFs(rel) }
function openFile(f) {
  if (f && f.kind === 'feature' && f.movie_id) router.push('/m/' + f.movie_id)
}
function fileInfo(rel) { return fsFiles.value.find(f => f.rel === rel) || null }
function dirInfo(rel) { return fsDirs.value.find(d => d.rel === rel) || null }

function copySelection() {
  if (!selRels.value.length) return
  clipboard.value = { mode: 'copy', rels: [...selRels.value] }
  fsMsg.value = `已复制 ${selRels.value.length} 项到剪贴板，进入目标目录 Ctrl+V 粘贴`
}
function cutSelection() {
  if (!selRels.value.length) return
  const dirs = selRels.value.filter(r => dirInfo(r))
  const files = selRels.value.filter(r => !dirInfo(r))
  clipboard.value = { mode: 'cut', rels: files }
  fsMsg.value = dirs.length
    ? `已剪切 ${files.length} 个文件（目录不支持移动，${dirs.length} 项已忽略）`
    : `已剪切 ${files.length} 项，进入目标目录 Ctrl+V 粘贴`
}

function paste() {
  if (!clipboard.value.rels.length || copyJob.value) return
  if (clipboard.value.mode === 'cut') doPasteMove()
  else doPasteCopy()
}

function _promptUnmatched(rels) {
  const bad = rels.map(fileInfo).filter(f => f && f.kind === 'feature' && !f.tmdb_id)
  if (bad.length) {
    fsPrompts.value.push({
      text: `${bad.length} 个未匹配正片已移动/复制，建议去匹配：${bad[0].name}`,
      movieId: bad[0].movie_id
    })
  }
}

async function doPasteMove() {
  const rels = [...clipboard.value.rels]
  busy.value = 'fs'
  let moved = 0
  const skipped = []
  try {
    for (const rel of rels) {
      const pv = await api('/api/fs/move', { method: 'POST', body: JSON.stringify({ from: rel, to_dir: fsPath.value, dry_run: true }) })
      const p = (pv.plans || [])[0] || {}
      if (p.status === 'conflict_disk_exists') { skipped.push(rel); continue }
      const d = await api('/api/fs/move', { method: 'POST', body: JSON.stringify({ from: rel, to_dir: fsPath.value, dry_run: false }) })
      const r = (d.results || [])[0] || {}
      if (r.status === 'moved') moved++
      else skipped.push(rel)
    }
    fsMsg.value = `已移动 ${moved} 项` + (skipped.length ? `，跳过 ${skipped.length} 项（同名冲突或不支持）` : '')
    if (fileInfo(rels[0]) && fileInfo(rels[0]).kind === 'feature') {
      fsPrompts.value.push({ text: '提示：分区不会随移动变化；如需按大区归位请用「入库流程 → ③ 归档整理」。' })
    }
    _promptUnmatched(rels)
    clipboard.value = { mode: 'copy', rels: [] }
    await loadFs(fsPath.value)
    emit('changed')
  } catch (e) {
    fsMsg.value = '粘贴（移动）失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doPasteCopy() {
  const rels = [...clipboard.value.rels]
  busy.value = 'fs'
  try {
    const pv = await api('/api/fs/copy', {
      method: 'POST',
      body: JSON.stringify({ from: rels, to_dir: fsPath.value, dry_run: true })
    })
    if (pv.needs_confirm || (pv.conflicts || []).length) {
      pendCopy.value = {
        rels,
        hint: `共 ${pv.total} 项 / ${pv.files} 个文件 / ${fmtBytes(pv.bytes)}`
          + ((pv.conflicts || []).length ? `，${pv.conflicts.length} 项同名将自动改「(副本)」` : '')
      }
      fsMsg.value = ''
      return
    }
    await startCopy(rels)
  } catch (e) {
    fsMsg.value = '复制预览失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function confirmCopy() {
  const p = pendCopy.value
  pendCopy.value = null
  if (!p) return
  busy.value = 'fs'
  try {
    await startCopy(p.rels)
  } catch (e) {
    fsMsg.value = '复制失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function startCopy(rels) {
  const d = await api('/api/fs/copy', {
    method: 'POST',
    body: JSON.stringify({ from: rels, to_dir: fsPath.value, dry_run: false })
  })
  copyJob.value = { jobId: d.job_id, total: d.total, bytesTotal: d.bytes, done: 0, bytesDone: 0,
                    hadDirs: rels.some(r => dirInfo(r)) }
  fsMsg.value = '复制中…（大文件可继续操作，进度见上方）'
  copyPoll.start()
}

async function pollCopy() {
  const j = copyJob.value
  if (!j) return
  try {
    const st = await api('/api/fs/copy/' + j.jobId)
    copyJob.value = { ...j, ...st, bytesTotal: st.bytes_total || j.bytesTotal, bytesDone: st.bytes_done || 0 }
    if (st.state === 'running') return
    copyPoll.stop()
    const renamed = st.renamed || 0
    const registered = st.registered || 0
    if (st.state === 'done') {
      fsMsg.value = `已粘贴 ${st.done}/${st.total} 项` + (renamed ? `，${renamed} 项改名「(副本)」` : '')
        + (registered ? `，${registered} 部正片已登记为新版本` : '')
    } else if (st.state === 'cancelled') {
      fsMsg.value = '复制已取消'
    } else {
      fsMsg.value = '复制失败：' + (st.error || '未知错误')
    }
    if (st.state === 'done' && (j.hadDirs || registered === 0)) {
      fsPrompts.value.push({ text: '复制完成：新文件尚未入库，建议扫描新增。', kind: 'scan' })
    }
    _promptUnmatched([...clipboard.value.rels])
    copyJob.value = null
    await loadFs(fsPath.value)
    emit('changed')
  } catch (e) { /* 轮询失败下次继续 */ }
}
const copyPoll = usePolling(pollCopy, { interval: 700 })

function cancelCopy() {
  const j = copyJob.value
  if (!j) return
  api('/api/fs/copy/' + j.jobId + '/cancel', { method: 'POST' }).catch(() => {})
}

async function deleteSelection() {
  if (!selRels.value.length) return
  const rels = [...selRels.value]
  const feats = rels.filter(r => { const f = fileInfo(r); return f && f.kind === 'feature' })
  for (const rel of rels) await doFsDelete(rel)
  if (feats.length) fsMsg.value = `含 ${feats.length} 部正片，需逐项两次确认（不可恢复）`
}

function onKeydown(e) {
  if (!props.active || isTextTarget(e)) return
  const mod = e.ctrlKey || e.metaKey
  if (mod && e.key.toLowerCase() === 'c') { copySelection(); e.preventDefault(); return }
  if (mod && e.key.toLowerCase() === 'x') { cutSelection(); e.preventDefault(); return }
  if (mod && e.key.toLowerCase() === 'v') { paste(); e.preventDefault(); return }
  if (e.key === 'Delete') { deleteSelection(); e.preventDefault(); return }
  if (e.key === 'Escape') { selRels.value = []; return }
  if (e.key === 'Enter') {
    const d = selRels.value.length === 1 ? dirInfo(selRels.value[0]) : null
    if (d) { enterDir(d.rel); e.preventDefault() }
    return
  }
  if (e.key === 'Backspace') {
    if (fsParent.value !== null) { loadFs(fsParent.value); e.preventDefault() }
    return
  }
  if (e.key === 'F5') { loadFs(fsPath.value); e.preventDefault() }
}

onMounted(() => {
  loadFs('')
  window.addEventListener('keydown', onKeydown)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  copyPoll.stop()
})
</script>

<style scoped>
/* 对话框/列表基础样式（R14-Q5 待统一：与 Settings 父级同名 scoped 规则一致） */
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.fhint { color: #777; font-size: 0.75rem; }
.miss-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.miss-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.miss-title { white-space: nowrap; }
.miss-path { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.kind-badge { font-size: 0.75rem; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; margin-left: 8px; color: #aaa; }
.kind-badge.bad { color: #ff8a8a; border-color: #6e2b2b; }
.fs-crumbs { flex-wrap: wrap; }
.linklike { background: none; border: none; color: #6ab0ff; cursor: pointer; padding: 0 2px; }
button.danger { border-color: #6e2b2b; color: #ff8a8a; }
.fs-file-row { flex-wrap: wrap; }
.fs-row { cursor: pointer; }
.fs-row.sel { border-color: #e50914; background: #2e2222; }
.fs-prompt { border: 1px dashed #6e2b2b; border-radius: 8px; padding: 6px 10px; }
.up-progress { height: 8px; border-radius: 999px; background: #2c2c2c; overflow: hidden; margin: 4px 0; }
.up-progress-fill { height: 100%; background: #e50914; border-radius: 999px; transition: width .2s; }
</style>
