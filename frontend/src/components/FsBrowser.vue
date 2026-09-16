<template>
      <section id="sec-files" class="card-block">
        <h3>文件浏览</h3>
        <p class="hint">直接翻目录动手：建子目录、改名、移动、删除，日常少用。花絮/字幕/周边直接删；正片删文件同时清库（影响海报墙），需二次确认。</p>
        <div class="bar fs-crumbs">
          <button @click="loadFs('')" :disabled="!!busy">根</button>
          <span v-for="c in fsCrumbs" :key="c.rel"> / <button @click="loadFs(c.rel)" :disabled="!!busy" class="linklike">{{ c.name }}</button></span>
          <span v-if="fsPath" class="miss-path">{{ fsPath }}</span>
        </div>
        <div class="bar">
          <input v-model="fsMkdirName" placeholder="新子目录名" style="width:160px" />
          <button @click="doFsMkdir" :disabled="!!busy || !fsMkdirName.trim()">新建目录</button>
          <input v-model="fsMoveDir" placeholder="移至目录（相对路径）" style="width:200px; margin-left:12px" />
        </div>
        <div v-if="fsParent !== null" class="bar">
          <button @click="loadFs(fsParent)" :disabled="!!busy">‹ 上级目录</button>
          <span>{{ fsMsg }}</span>
        </div>
        <ul v-if="fsDirs.length" class="miss-list">
          <li v-for="d in fsDirs" :key="'d' + d.rel" class="miss-row">
            <span class="miss-title">📁 {{ d.name }}</span>
            <span class="miss-path">{{ d.children }} 项</span>
            <button @click="loadFs(d.rel)" :disabled="!!busy">进入</button>
            <button @click="doFsDelete(d.rel)" :disabled="!!busy">删空目录</button>
          </li>
        </ul>
        <ul v-if="fsFiles.length" class="miss-list">
          <li v-for="f in fsFiles" :key="'f' + f.rel" class="miss-row fs-file-row">
            <span v-if="f.kind === 'feature'" class="kind-badge bad">正片</span>
            <span v-else-if="f.kind === 'sidecar'" class="kind-badge">花絮</span>
            <span v-else-if="f.kind === 'subtitle'" class="kind-badge">字幕</span>
            <span v-else-if="f.kind === 'nfo'" class="kind-badge">NFO</span>
            <span v-else class="kind-badge">其他</span>
            <span class="miss-title">{{ f.name }}</span>
            <span class="miss-path">{{ fmtBytes(f.size) }}{{ f.title ? ` · ${f.title}` : '' }}</span>
            <input v-model="fsRenameEdits[f.rel]" placeholder="新文件名" style="width:140px" />
            <button @click="doFsRename(f.rel)" :disabled="!!busy">{{ fsArmAction('rename', f.rel) ? '确认改名' : '改名' }}</button>
            <button @click="doFsMove(f.rel)" :disabled="!!busy || !fsMoveDir.trim()">{{ fsArmAction('move', f.rel) ? '确认移动' : '移动' }}</button>
            <button v-if="!fsArmDelete[f.rel]" @click="doFsDelete(f.rel)" :disabled="!!busy">删除</button>
            <button v-else @click="doFsDeleteConfirm(f.rel)" :disabled="!!busy" class="danger">确认删除正片</button>
          </li>
        </ul>
        <p v-if="fsArmHint" class="hint warn-text">{{ fsArmHint }}</p>
        <p v-if="!fsDirs.length && !fsFiles.length" class="hint">空目录</p>
      </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'

// 文件浏览（评审 R14-Q2）：从 Settings.vue 抽出，独立自足；删除正片后发 changed 让父页刷新统计。
const emit = defineEmits(['changed'])
const busy = ref(null)

const fsPath = ref('')
const fsParent = ref('')
const fsCrumbs = ref([])
const fsDirs = ref([])
const fsFiles = ref([])
const fsMsg = ref('')
const fsMkdirName = ref('')
const fsMoveDir = ref('')
const fsRenameEdits = ref({})
const fsArmDelete = ref({})
const fsArmHint = ref('')
function fsKindText(k) {
  return { feature: '正片', sidecar: '花絮', subtitle: '字幕', nfo: 'NFO', other: '其他', dir: '目录' }[k] || k
}
async function loadFs(path) {
  fsMsg.value = ''
  fsArmHint.value = ''
  fsArmDelete.value = {}
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
    fsMsg.value = '已创建'
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
async function doFsMove(rel) {
  const toDir = fsMoveDir.value.trim()
  if (!toDir) {
    fsMsg.value = '先填移至目录'
    return
  }
  const armedKey = 'move:' + rel
  if (!fsArmAction('move', rel)) {
    busy.value = 'fs'
    try {
      const d = await api('/api/fs/move', { method: 'POST', body: JSON.stringify({ from: rel, to_dir: toDir, dry_run: true }) })
      const p = (d.plans || [])[0] || {}
      fsArmDelete.value = { ...fsArmDelete.value, [armedKey]: p }
      fsArmHint.value = p.status === 'conflict_disk_exists'
        ? `目标已存在：${p.to || ''}`
        : `将移动到：${p.to || toDir}。再点「确认移动」执行`
    } catch (e) {
      fsMsg.value = '移动预览失败：' + e.message
    } finally {
      busy.value = null
    }
    return
  }
  delete fsArmDelete.value[armedKey]
  busy.value = 'fs'
  try {
    const d = await api('/api/fs/move', { method: 'POST', body: JSON.stringify({ from: rel, to_dir: toDir, dry_run: false }) })
    const r = (d.results || [])[0] || {}
    fsMsg.value = r.status === 'moved' ? '已移动' : ('移动：' + (r.status || '失败'))
    await loadFs(fsPath.value)
    emit('changed')
  } catch (e) {
    fsMsg.value = '移动失败：' + e.message
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
    fsMsg.value = r.status === 'deleted' ? `已删除（${fsKindText(p.kind)}）` : ('删除：' + (r.status || '失败'))
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
    fsMsg.value = r.status === 'deleted' ? '正片已删除，库已同步清理' : ('删除：' + (r.status || '失败'))
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

onMounted(() => { loadFs('') })
</script>

<style scoped>
/* 对话框/列表基础样式（R14-Q5 待统一：与 Settings 父级同名 scoped 规则一致） */
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
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
</style>
