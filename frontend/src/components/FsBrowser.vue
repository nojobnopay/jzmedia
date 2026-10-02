<template>
  <section :id="active ? 'sec-files' : undefined" ref="root" class="fs-browser" tabindex="0" aria-label="文件管理器" @keydown="onKeydown" @pointerdown="closeOutsideMenu">
    <header class="fs-heading">
      <div><h3>文件管理</h3><p>浏览、复制和整理文件；变更后扫描核对入库与匹配结果。</p></div>
      <details class="fs-help"><summary>操作说明</summary><p>双击打开，Ctrl / ⌘ 多选，Shift 连选。Ctrl / ⌘ + A 全选、C 复制、X 剪切、V 粘贴；F2 改名、Delete 删除、Backspace 上级、F5 刷新。快捷键仅在文件管理器内生效。目录仅支持复制、删除空目录；改名或移动目录请使用归档整理。</p></details>
    </header>
    <div v-if="pending" class="fs-pending" role="status"><span><strong>{{ pendingChanges.count || 1 }} 项文件变更待扫描</strong><small>{{ library?.name }} · 扫描后继续核对匹配结果</small></span><button :disabled="hasPendingOperation" @click="scan">扫描并核对</button></div>
    <div class="fs-shell">
      <nav class="fs-tree" aria-label="视频库目录">
        <span class="fs-tree-title">视频库</span>
        <template v-for="lib in videoLibs" :key="lib.id">
          <div :class="['fs-tree-line', { current: libraryId === Number(lib.id) && !path }]">
            <button v-if="libraryId === Number(lib.id)" class="fs-expander" :aria-label="treeExpanded.has('') ? '收起目录' : '展开目录'" :aria-expanded="treeExpanded.has('')" @click="toggleTree('')">{{ treeExpanded.has('') ? '▾' : '▸' }}</button><span v-else class="fs-expander"></span>
            <button class="fs-tree-name" :disabled="busy" @click="switchLibrary(lib.id)"><span aria-hidden="true">▣</span> {{ libraryName(lib.id) }}<small>{{ lib.kind === 'tv' ? '剧集' : '电影' }}</small></button>
          </div>
          <template v-if="libraryId === Number(lib.id)">
            <div v-for="dir in treeRows" :key="dir.rel" :class="['fs-tree-line', { current: path === dir.rel }]" :style="{ paddingLeft: (dir.depth * 13) + 'px' }">
              <button class="fs-expander" :aria-label="(treeExpanded.has(dir.rel) ? '收起 ' : '展开 ') + dir.name" :aria-expanded="treeExpanded.has(dir.rel)" :disabled="treeLoading.has(dir.rel)" @click="toggleTree(dir.rel)"><Spinner v-if="treeLoading.has(dir.rel)" :size="12" /><template v-else>{{ treeExpanded.has(dir.rel) ? '▾' : '▸' }}</template></button>
              <button class="fs-tree-name" :title="dir.rel" :disabled="busy" @click="navigate(dir.rel)"><span aria-hidden="true">▱</span> {{ dir.name }}</button>
            </div>
          </template>
        </template>
      </nav>
      <div class="fs-main">
        <div class="fs-location">
          <div class="fs-navigation"><button title="返回" aria-label="返回" :disabled="busy || historyIndex <= 0" @click="historyMove(-1)">←</button><button title="前进" aria-label="前进" :disabled="busy || historyIndex >= history.length - 1" @click="historyMove(1)">→</button><button title="上级目录" aria-label="上级目录" :disabled="busy || !path" @click="up">↑</button><button title="刷新目录" aria-label="刷新目录" :disabled="busy || loading" @click="load(loadError ? loadTarget : path)">↻</button></div>
          <nav class="fs-crumbs" aria-label="当前位置"><button :disabled="busy" @click="navigate('')">{{ library?.name || '选择视频库' }}</button><template v-for="crumb in crumbs" :key="crumb.rel"><span aria-hidden="true">/</span><button :disabled="busy" @click="navigate(crumb.rel)">{{ crumb.name }}</button></template></nav>
          <input v-model="query" class="fs-search" type="search" aria-label="搜索当前目录" placeholder="搜索当前目录" :disabled="loading || !!loadError || !hasLoaded" />
        </div>
        <div class="fs-toolbar" role="toolbar" aria-label="文件操作">
          <button :disabled="!one || busy || loading || !!loadError" @click="open()">{{ one?.isDir ? '打开文件夹' : '预览' }}</button>
          <button :disabled="!writable || busy || loading || !!loadError" @click="beginMkdir">新建文件夹</button>
          <button :disabled="!selection.length || busy || loading || !!loadError" @click="copy('copy')">复制</button>
          <button :disabled="!canCut" :title="selectedRows.some(r => r.isDir) ? '目录移动请使用归档整理' : '剪切选中文件'" @click="copy('cut')">剪切</button>
          <button :disabled="!canPaste" @click="paste">粘贴{{ clipboard.mode === 'cut' ? '（移动）' : '' }}</button>
          <button class="fs-secondary-action" :disabled="!canRename" :title="one?.isDir ? '目录改名请使用归档整理' : '重命名（F2）'" @click="beginRename">重命名</button>
          <button class="fs-secondary-action" :disabled="!writable || !selection.length || busy || loading || !!loadError" @click="beginDelete">删除…</button>
          <button ref="moreButton" aria-label="更多文件操作" :aria-expanded="!!menu" @click="openMore">更多 ⋯</button>
          <span v-if="!writable && hasLoaded && !loading && !loadError && libraryId" class="fs-readonly">只读</span>
        </div>
        <p v-if="message" class="fs-message" role="status">{{ message }}</p>
        <div v-if="prompt" ref="promptEl" class="fs-prompt" role="region" aria-label="文件操作确认">
          <div class="fs-prompt-heading"><strong>{{ promptTitle }}</strong><button :disabled="busy" aria-label="取消操作" @click="prompt = null">×</button></div>
          <p class="fs-target">{{ library?.name }} / {{ (prompt.to_dir ?? prompt.path ?? path) || '根目录' }}</p>
          <form v-if="prompt.type === 'rename' || prompt.type === 'mkdir'" @submit.prevent="submitName">
            <label :for="inputId">{{ prompt.type === 'rename' ? '新文件名' : '文件夹名称' }}</label><input :id="inputId" ref="nameInput" v-model="inputName" :disabled="busy" autocomplete="off" />
            <template v-if="prompt.plans"><p v-for="plan in prompt.plans" :key="plan.from" class="fs-plan">{{ plan.from }} → {{ plan.to }}<strong v-if="plan.status !== 'planned'"> · {{ statusText(plan.status) }}</strong></p><template v-for="plan in prompt.plans" :key="'followers-' + plan.from"><p v-for="follower in plan.followers || []" :key="follower.from" class="fs-plan fs-follower">关联文件：{{ follower.from }} → {{ follower.to }}<span v-if="follower.status !== 'planned'"> · {{ statusText(follower.status) }}，保留原文件</span></p></template></template>
            <div class="fs-confirm-actions"><button :disabled="busy || !inputName.trim() || (prompt.plans && (!prompt.plans.length || !prompt.plans.every(p => p.status === 'planned')))" type="submit">{{ prompt.type === 'mkdir' ? '创建文件夹' : prompt.plans ? '确认改名' : '预览改名' }}</button><button type="button" :disabled="busy" @click="prompt = null">取消</button></div>
          </form>
          <template v-else-if="prompt.type === 'delete'">
            <p class="fs-warning">文件会被物理删除，无法恢复。正片的入库记录和播放进度也会清理；非空目录不会删除。</p>
            <ul class="fs-plan-list"><li v-for="plan in prompt.plans" :key="plan.rel"><strong v-if="plan.requires_confirm">正片 · </strong>{{ plan.title || plan.name || plan.rel }}<small>{{ plan.rel }}</small><span v-if="plan.version_count > 1"> · {{ plan.version_count }} 个版本中的 1 个</span><span v-if="plan.status && plan.status !== 'planned_rmdir'"> · {{ statusText(plan.status) }}</span></li></ul>
            <div class="fs-confirm-actions"><button class="danger" :disabled="busy || !prompt.plans.some(p => !p.status || p.status === 'planned_rmdir')" @click="confirmDelete">确认永久删除</button><button :disabled="busy" @click="prompt = null">取消</button></div>
          </template>
          <template v-else-if="prompt.type === 'copy'">
            <p>共 {{ prompt.preview.total }} 项 / {{ prompt.preview.files }} 个文件 / {{ fmtBytes(prompt.preview.bytes) }}。同名项目自动添加“(副本)”，保留已有文件。</p>
            <p v-if="prompt.preview.conflicts?.length">{{ prompt.preview.conflicts.length }} 项同名冲突将自动改名。</p>
            <div class="fs-confirm-actions"><button :disabled="busy" @click="confirmPaste">确认复制到此目录</button><button :disabled="busy" @click="prompt = null">取消</button></div>
          </template>
          <template v-else-if="prompt.type === 'move'">
            <ul class="fs-plan-list"><li v-for="plan in prompt.plans" :key="plan.from">{{ plan.from }} → {{ plan.to || prompt.to_dir || '根目录' }}<strong v-if="plan.status !== 'planned'"> · {{ statusText(plan.status) }}</strong><small v-for="follower in plan.followers || []" :key="follower.from">关联文件：{{ follower.from }} → {{ follower.to }}{{ follower.status !== 'planned' ? ' · 冲突，保留原文件' : '' }}</small></li></ul>
            <p>匹配结果保持不变；完成后扫描核对。目标已存在的项目会跳过。</p>
            <div class="fs-confirm-actions"><button :disabled="busy || !prompt.plans.some(p => p.status === 'planned')" @click="confirmPaste">确认移动</button><button :disabled="busy" @click="prompt = null">取消</button></div>
          </template>
        </div>
        <div class="fs-table-area">
        <div class="fs-table-wrap" :aria-busy="loading" @contextmenu.prevent="openContext($event)" @click.self="resetSelection">
          <table class="fs-table" :inert="loading || !!loadError || !hasLoaded" aria-label="当前目录文件" aria-multiselectable="true">
            <thead><tr><th class="fs-check"><input type="checkbox" aria-label="全选当前目录" :checked="!!rows.length && selection.length === rows.length" :disabled="loading || !!loadError || !hasLoaded" @change="$event.target.checked ? selectAll() : resetSelection()" /></th><th v-for="col in columns" :key="col.key" :class="'fs-col-' + col.key" :aria-sort="sort === col.key ? (direction === 1 ? 'ascending' : 'descending') : 'none'"><button @click="setSort(col.key)">{{ col.label }}<span v-if="sort === col.key" aria-hidden="true"> {{ direction === 1 ? '↑' : '↓' }}</span></button></th></tr></thead>
            <tbody><tr v-for="row in rows" :key="row.rel" class="fs-row" :class="{ selected: selection.includes(row.rel), focused: focused === row.rel, cut: clipboard.mode === 'cut' && clipboard.library_id === libraryId && clipboard.rels.includes(row.rel) }" :data-fs-rel="row.rel" tabindex="-1" :aria-selected="selection.includes(row.rel)" @click="rowClick($event, row)" @dblclick="open(row)" @contextmenu.stop.prevent="openContext($event, row)">
              <td class="fs-check"><input type="checkbox" :aria-label="'选择 ' + row.name" :checked="selection.includes(row.rel)" :disabled="loading || !!loadError || !hasLoaded" @click.stop="select(row, { ctrlKey: true })" @dblclick.stop /></td>
              <td class="fs-name" :title="row.name"><span class="fs-file-icon" :class="{ folder: row.isDir }" aria-hidden="true">{{ row.isDir ? '▱' : '▤' }}</span><span>{{ row.name }}</span></td>
              <td class="fs-col-size">{{ row.isDir ? '—' : fmtBytes(row.size) }}</td><td class="fs-col-mtime">{{ formatDate(row.mtime) }}</td><td class="fs-col-type">{{ fsType(row) }}</td><td class="fs-col-status"><span :class="{ 'fs-unmatched': fsMatchStatus(row) === '待匹配', 'fs-unregistered': fsMatchStatus(row) === '待扫描' }">{{ fsMatchStatus(row) }}</span></td>
            </tr></tbody>
          </table>
          <p v-if="loadState === 'ready' && !rows.length" class="fs-empty">{{ query ? '当前目录没有符合搜索的项目' : '此文件夹为空' }}</p>
        </div>
        <div v-if="loadState === 'loading' || loadState === 'waiting'" class="fs-directory-state" role="status" aria-live="polite">
          <Spinner :size="34" /><strong>{{ loadState === 'waiting' ? '正在准备目录…' : '正在加载目录…' }}</strong>
          <template v-if="totalCount > 0"><span>已读取 {{ loadedCount }} / {{ totalCount }} 个文件</span><progress :value="loadedCount" :max="totalCount" aria-label="目录加载进度"></progress></template>
          <span v-else>正在连接并读取文件列表，请稍候。</span>
        </div>
        <div v-else-if="loadState === 'error'" class="fs-directory-state fs-directory-error" role="alert">
          <strong>目录加载失败</strong><span>{{ library?.name }} / {{ loadTarget || '根目录' }}</span><p>{{ loadError }}</p><button :disabled="busy" @click="retryLoad">重试加载目录</button>
        </div>
        <div v-else-if="loadState === 'unselected'" class="fs-directory-state"><p>请先选择一个视频库</p></div>
        </div>
        <footer class="fs-status"><span v-if="loadState === 'loading'">正在加载目录…</span><span v-else-if="loadState === 'error'">目录未能加载</span><span v-else-if="loadState !== 'ready'">等待读取目录</span><span v-else>{{ rows.length }} 项{{ query ? `（共 ${dirs.length + files.length} 项）` : '' }}<template v-if="selection.length"> · 已选 {{ selection.length }} 项</template></span><span v-if="clipboard.rels.length">{{ clipboard.mode === 'cut' ? '已剪切' : '已复制' }} {{ clipboard.rels.length }} 项{{ clipboard.library_id !== libraryId ? '（其他视频库）' : '' }}</span></footer>
      </div>
    </div>
    <div v-if="jobs.length" class="fs-tasks" aria-label="文件任务"><div v-for="job in jobs" :key="job.job_id" class="fs-task"><span>{{ jobText(job) }}<small>目标：{{ libraryName(job.library_id) }} / {{ job.to_dir || '根目录' }}</small></span><progress :value="jobProgress(job)" max="100" :aria-label="jobText(job)"></progress><button v-if="jobRunning(job)" :disabled="job.state === 'cancelled'" @click="cancelCopy(job)">{{ job.state === 'cancelled' ? '正在取消…' : '取消复制' }}</button><button v-else @click="jobs = jobs.filter(j => j.job_id !== job.job_id)">收起</button><details v-if="jobIssues(job).length" class="fs-task-issues"><summary>查看需核对项目（{{ jobIssues(job).length }}）</summary><ul><li v-for="item in jobIssues(job)" :key="item.from">{{ item.from }} → {{ item.to }}<small>{{ jobIssueText(item) }}</small></li></ul></details></div></div>
    <FilePreviewHost :file="previewFile" :active="active" @close="closeFilePreview" />
    <Teleport to="body"><div v-if="menu" ref="menuEl" class="fs-context-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" role="menu" aria-label="文件菜单" @keydown="onMenuKeydown" @contextmenu.prevent><button v-for="item in menuItems" :key="item.id" role="menuitem" :disabled="item.disabled" :title="item.hint || ''" @click="runMenu(item)"><span>{{ item.label }}</span><small>{{ item.shortcut }}</small></button></div></Teleport>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch, useId } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtBytes } from '../format.js'
import { fsMatchStatus, fsType } from '../fsBrowser.js'
import { useFsBrowser } from '../useFsBrowser.js'
import FilePreviewHost from './FilePreviewHost.vue'
import Spinner from './Spinner.vue'

const props = defineProps({ active: { type: Boolean, default: false }, media: { type: Object, default: null }, videoLibs: { type: Array, default: () => [] }, initialLibId: { type: Number, default: null }, pendingChanges: { type: Object, default: null } })
const emit = defineEmits(['changed', 'scan', 'library-change'])
const {
  libraryId, library, path, dirs, files, crumbs, writable, loading, hasLoaded, loadError, loadTarget, loadState, loadedCount, totalCount, message, busy, query, sort, direction,
  selection, focused, clipboard, prompt, previewFile, inputName, jobs, history, historyIndex, treeExpanded, treeLoading, rows, one, selectedRows,
  canRename, canCut, canPaste, pending, hasRunningTask, hasPendingOperation, treeRows,
  scan, switchLibrary, navigate, load, retryLoad, historyMove, up, toggleTree, select, selectAll, moveFocus, open, viewDetails, closePreview, setSort, copy, copyPath,
  beginRename, beginMkdir, submitName, beginDelete, confirmDelete, paste, confirmPaste, cancelCopy, jobProgress, jobText, jobRunning, jobIssues, resetSelection,
} = useFsBrowser(props, emit, api, useRouter())
const root = ref(null)
const menuEl = ref(null)
const moreButton = ref(null)
const nameInput = ref(null)
const promptEl = ref(null)
const menu = ref(null)
const inputId = 'fs-name-' + useId()
const columns = [{ key: 'name', label: '名称' }, { key: 'size', label: '大小' }, { key: 'mtime', label: '修改时间' }, { key: 'type', label: '类型' }, { key: 'status', label: '入库 / 匹配' }]
const promptTitle = computed(() => ({ rename: '重命名文件', mkdir: '新建文件夹', delete: '删除确认', copy: '复制确认', move: '移动确认' }[prompt.value?.type] || '确认操作'))
const menuItems = computed(() => [
  { id: 'open', label: one.value?.isDir ? '打开文件夹' : '预览', disabled: !one.value || busy.value || loading.value || !!loadError.value, shortcut: 'Enter', run: () => open() },
  { id: 'details', label: '查看媒体详情', disabled: !one.value || one.value.isDir || (!one.value.movie_id && !one.value.show_id) || busy.value || loading.value || !!loadError.value, run: () => viewDetails() },
  { id: 'cut', label: '剪切', disabled: !canCut.value, hint: selectedRows.value.some(r => r.isDir) ? '目录移动请使用归档整理' : '', shortcut: 'Ctrl+X', run: () => copy('cut') },
  { id: 'copy', label: '复制', disabled: !selection.value.length || busy.value || loading.value || !!loadError.value, shortcut: 'Ctrl+C', run: () => copy('copy') },
  { id: 'paste', label: clipboard.value.mode === 'cut' ? '粘贴（移动）' : '粘贴', disabled: !canPaste.value, shortcut: 'Ctrl+V', run: paste },
  { id: 'rename', label: '重命名', disabled: !canRename.value, hint: one.value?.isDir ? '目录改名请使用归档整理' : '', shortcut: 'F2', run: beginRename },
  { id: 'delete', label: '删除…', disabled: !writable.value || !selection.value.length || busy.value || loading.value || !!loadError.value, shortcut: 'Delete', run: beginDelete },
  { id: 'mkdir', label: '新建文件夹', disabled: !writable.value || busy.value || loading.value || !!loadError.value, run: beginMkdir },
  { id: 'path', label: '复制库内路径', disabled: libraryId.value == null, run: copyPath },
  { id: 'refresh', label: '刷新', disabled: busy.value || loading.value, shortcut: 'F5', run: () => load(loadError.value ? loadTarget.value : path.value) },
].filter(item => selection.value.length || ['paste', 'mkdir', 'path', 'refresh'].includes(item.id)))
function jobIssueText(item) {
  return item.status.startsWith('copied_scan_warn:') ? '文件已复制，入库失败：' + item.status.slice(17) : '复制失败：' + item.status.replace(/^error:\s*/, '')
}
function formatDate(seconds) {
  if (!seconds) return '—'
  return new Date(Number(seconds) * 1000).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function libraryName(id) {
  const lib = props.videoLibs.find(item => Number(item.id) === Number(id))
  if (!lib) return '视频库 ' + id
  const multipleMedia = new Set(props.videoLibs.map(item => item.media_library_id ?? item.media_id)).size > 1
  return multipleMedia && lib.media_name ? `${lib.media_name} · ${lib.name}` : lib.name
}
function statusText(status) { return ({ conflict_disk_exists: '目标已存在', conflict_db_occupied: '目标已有媒体记录，请先扫描核对', dir_not_empty: '目录非空，跳过', skipped_missing_src: '文件已不存在' }[status] || status) }
function rowClick(event, row) { if (loading.value) return; select(row, event); event.currentTarget.focus() }
function focusCurrent(options) { nextTick(() => [...(root.value?.querySelectorAll('[data-fs-rel]') || [])].find(el => el.getAttribute('data-fs-rel') === focused.value)?.focus(options)) }
let previewScroll = null
watch(previewFile, (file, previous) => {
  const table = root.value?.querySelector('.fs-table-wrap')
  if (file && !previous) previewScroll = { top: table?.scrollTop || 0, left: table?.scrollLeft || 0, pageX: window.scrollX, pageY: window.scrollY }
  if (!file && previous && props.active) {
    const saved = previewScroll
    nextTick(() => {
      if (table && saved) { table.scrollTop = saved.top; table.scrollLeft = saved.left }
      if (saved) window.scrollTo({ left: saved.pageX, top: saved.pageY, behavior: 'instant' })
      if (!document.querySelector('[role="dialog"][aria-modal="true"]')) focusCurrent({ preventScroll: true })
    })
  }
})
function closeFilePreview() { closePreview() }
async function showMenu(x, y) {
  menu.value = { x: Math.max(8, Math.min(x, window.innerWidth - 230)), y: Math.max(8, y) }
  await nextTick()
  if (!menu.value || !menuEl.value) return
  const rect = menuEl.value.getBoundingClientRect()
  menu.value.y = Math.max(8, Math.min(menu.value.y, window.innerHeight - rect.height - 8))
  menuEl.value.querySelector('button:not(:disabled)')?.focus()
}
function openContext(event, row = null) {
  if (row && !selection.value.includes(row.rel)) select(row)
  if (!row) resetSelection()
  showMenu(event.clientX, event.clientY)
}
function openMore() {
  if (menu.value) { menu.value = null; return }
  const rect = moreButton.value.getBoundingClientRect()
  showMenu(rect.left, rect.bottom + 4)
}
function closeOutsideMenu(event) { if (menu.value && !menuEl.value?.contains(event.target) && event.target !== moreButton.value) menu.value = null }
function runMenu(item) { if (item.disabled) return; menu.value = null; root.value?.focus(); item.run() }
function onMenuKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); menu.value = null; root.value?.focus() }
  else if (event.key === 'Tab') menu.value = null
  else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    const buttons = [...menuEl.value.querySelectorAll('button:not(:disabled)')]
    const current = buttons.indexOf(document.activeElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
    buttons[next]?.focus()
  }
}
function onKeydown(event) {
  if (!props.active || previewFile.value || menu.value || prompt.value || event.target.closest('textarea,select,[contenteditable="true"]')) return
  if (event.target.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(event.target.type)) return
  if (event.target.closest('button') && !['Escape', 'F2', 'F5'].includes(event.key)) return
  const key = event.key.toLowerCase()
  const mod = event.ctrlKey || event.metaKey
  const handled = () => event.preventDefault()
  if (mod && key === 'a') { handled(); selectAll() }
  else if (mod && key === 'c') { handled(); copy('copy') }
  else if (mod && key === 'x') { handled(); copy('cut') }
  else if (mod && key === 'v') { handled(); paste() }
  else if (event.key === 'Delete') { handled(); beginDelete() }
  else if (event.key === 'F2') { handled(); beginRename() }
  else if (event.key === 'F5') { handled(); load(loadError.value ? loadTarget.value : path.value) }
  else if (event.key === 'Backspace') { handled(); up() }
  else if (event.key === 'Enter') { handled(); open() }
  else if (event.key === 'Escape') { handled(); resetSelection() }
  else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { handled(); moveFocus(event.key === 'ArrowDown' ? 1 : -1, event); focusCurrent() }
  else if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { handled(); const rect = root.value.getBoundingClientRect(); showMenu(rect.left + Math.min(rect.width / 2, 280), rect.top + 180) }
}
watch(prompt, async value => { if (value) { await nextTick(); nameInput.value?.focus(); promptEl.value?.scrollIntoView?.({ block: 'nearest' }) } })
watch(() => props.active, active => { if (!active) menu.value = null })
watch(libraryId, () => { menu.value = null })
onMounted(() => window.addEventListener('pointerdown', closeOutsideMenu))
onUnmounted(() => window.removeEventListener('pointerdown', closeOutsideMenu))
defineExpose({ closePreview: closeFilePreview, refresh: () => load(loadError.value ? loadTarget.value : path.value), hasRunningTask: () => hasRunningTask.value, hasPendingOperation: () => hasPendingOperation.value, currentLibraryId: () => libraryId.value })
</script>

<style scoped>
.fs-browser { color: #d6d9df; background: #1c1e23; border: 1px solid #343840; border-radius: 12px; overflow: hidden; outline: none; }
.fs-browser:focus-visible { outline: 2px solid #7daced; outline-offset: 2px; }
.fs-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding: 18px 20px; }
.fs-heading h3 { margin: 0 0 6px; font-size: 1.08rem; color: #f0f1f4; }
.fs-heading p { color: #a7aeba; margin: 0; font-size: .82rem; line-height: 1.6; }
.fs-help { max-width: 420px; font-size: .8rem; color: #aeb6c3; }
.fs-help summary { cursor: pointer; white-space: nowrap; }
.fs-help p { padding-top: 8px; }
.fs-pending { margin: 0 16px 14px; padding: 12px 14px; background: #3b3020; border: 1px solid #68512e; border-radius: 8px; display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: .87rem; }
.fs-pending small { display: block; margin-top: 4px; color: #c7bba5; }
.fs-shell { display: grid; grid-template-columns: 210px minmax(0, 1fr); border-top: 1px solid #343840; }
.fs-tree { display: block; background: #191b20; border-right: 1px solid #343840; padding: 14px 8px; overflow: auto; max-height: 680px; }
.fs-tree-title { display: block; margin: 0 8px 12px; font-size: .8rem; color: #aab3c1; font-weight: 600; }
.fs-tree-line { display: flex; align-items: center; min-height: 36px; border-radius: 5px; }
.fs-tree-line.current { background: #2a3443; }
.fs-tree-line .fs-expander { width: 22px; min-width: 22px; padding: 4px 2px; border: 0; background: none; color: #aab4c2; }
.fs-tree-name { background: none; border: 0; border-radius: 0; padding: 8px 3px; color: #d0d7e2; font-size: .82rem; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; flex: 1; }
.fs-tree-name small { margin-left: 8px; font-size: .67rem; color: #8997aa; }
.fs-main { min-width: 0; display: flex; flex-direction: column; }
.fs-location { padding: 10px 12px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; border-bottom: 1px solid #343840; }
.fs-navigation { display: flex; gap: 3px; }
.fs-navigation button { width: 30px; padding: 4px 0; font-size: 1rem; }
.fs-crumbs { padding: 0; background: transparent; flex: 1; min-width: 140px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px; color: #747f90; }
.fs-crumbs button { background: transparent; border: 0; padding: 3px 4px; color: #c2d1e5; font-size: .8rem; overflow-wrap: anywhere; }
.fs-search { width: 166px; min-width: 0; font-size: .8rem; padding: 7px 9px; }
.fs-toolbar { display: flex; gap: 6px; flex-wrap: wrap; padding: 10px 12px; align-items: center; }
.fs-browser button { cursor: pointer; }
.fs-browser button:disabled { cursor: default; opacity: .42; }
.fs-toolbar button, .fs-confirm-actions button, .fs-pending button, .fs-task button { padding: 6px 10px; font-size: .78rem; }
.fs-readonly { margin-left: auto; font-size: .75rem; color: #b8a072; }
.fs-message { margin: 0; padding: 9px 14px; color: #becbda; background: #25303e; font-size: .8rem; overflow-wrap: anywhere; }
.fs-prompt { border: 1px solid #536477; background: #242a33; margin: 10px 12px; border-radius: 8px; padding: 12px; font-size: .83rem; }
.fs-prompt-heading { display: flex; justify-content: space-between; align-items: center; }
.fs-prompt-heading button { background: none; border: 0; font-size: 1.2rem; padding: 0 5px; }
.fs-target { color: #9dadc1; font-size: .76rem; overflow-wrap: anywhere; }
.fs-prompt label { display: block; margin: 10px 0 6px; }
.fs-prompt input { box-sizing: border-box; width: 100%; max-width: 540px; }
.fs-confirm-actions { display: flex; gap: 8px; margin-top: 12px; }
.fs-warning { color: #efb787; line-height: 1.6; }
.fs-plan, .fs-plan-list { overflow-wrap: anywhere; line-height: 1.7; }
.fs-follower { color: #a3b5ca; font-size: .78rem; }
.fs-plan-list { max-height: 200px; overflow: auto; padding-left: 22px; }
.fs-plan-list small { display: block; color: #9dadc1; }
.fs-table-area { position: relative; display: flex; flex-direction: column; min-height: 260px; flex: 1; }
.fs-directory-state { position: absolute; inset: 0; z-index: 2; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; padding: 24px; box-sizing: border-box; background: #1c1e23f2; color: #aebfd5; text-align: center; font-size: .85rem; }
.fs-directory-state strong { color: #e0e7f0; font-size: .96rem; }
.fs-directory-state span, .fs-directory-state p { overflow-wrap: anywhere; max-width: 100%; margin: 0; }
.fs-directory-state progress { width: min(240px, 80%); height: 6px; accent-color: #7398d6; }
.fs-directory-error p { color: #e6b885; max-height: 100px; overflow: auto; }
.fs-directory-error button { padding: 8px 14px; }
.fs-table-wrap { overflow: auto; position: relative; min-height: 260px; max-height: 540px; flex: 1; border-top: 1px solid #343840; }
.fs-table { border-collapse: collapse; width: 100%; table-layout: fixed; font-size: .79rem; }
.fs-table th { text-align: left; position: sticky; top: 0; background: #23262d; z-index: 1; border-bottom: 1px solid #3e434e; font-weight: 500; }
.fs-table th button { border: 0; background: none; padding: 10px 6px; color: #b7c1d0; font: inherit; white-space: nowrap; }
.fs-table td { padding: 10px 6px; border-bottom: 1px solid #292d35; color: #b7bfca; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fs-table .fs-check { width: 32px; padding: 0 5px 0 10px; }
.fs-check input { margin: 0; accent-color: #729dd9; }
.fs-table .fs-col-name { min-width: 160px; }
.fs-table .fs-col-size { width: 82px; }
.fs-table .fs-col-mtime { width: 142px; }
.fs-table .fs-col-type { width: 60px; }
.fs-table .fs-col-status { width: 86px; }
.fs-table td.fs-name { color: #e0e5ed; }
.fs-file-icon { margin-right: 9px; color: #8498b5; font-size: 1rem; }
.fs-file-icon.folder { color: #d7b26c; }
.fs-row { cursor: default; user-select: none; outline: none; }
.fs-row:hover { background: #252b35; }
.fs-row.selected { background: #293b55; }
.fs-row:focus-visible { outline: 1px solid #85acd9; outline-offset: -1px; }
.fs-row.cut { opacity: .5; }
.fs-unmatched { color: #e0b574; }
.fs-unregistered { color: #bba8e2; }
.fs-empty { text-align: center; padding: 64px 16px; font-size: .85rem; color: #93a0b1; }
.fs-status { padding: 10px 14px; display: flex; justify-content: space-between; gap: 12px; color: #9ba7b8; font-size: .75rem; border-top: 1px solid #343840; }
.fs-tasks { padding: 12px 16px; border-top: 1px solid #343840; background: #212630; }
.fs-task { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 6px 0; font-size: .8rem; }
.fs-task span { flex: 1; min-width: 150px; }
.fs-task small { display: block; color: #93a3b9; margin-top: 4px; }
.fs-task-issues { flex-basis: 100%; color: #e0b574; overflow-wrap: anywhere; }
.fs-task-issues summary { cursor: pointer; }
.fs-task-issues ul { max-height: 200px; overflow: auto; padding-left: 20px; }
.fs-task progress { height: 7px; width: 140px; accent-color: #7398d6; }
.fs-context-menu { position: fixed; z-index: 10050; width: 220px; box-sizing: border-box; max-height: calc(100vh - 16px); overflow-y: auto; padding: 6px; background: #292e38; border: 1px solid #586376; box-shadow: 0 12px 32px #0008; border-radius: 8px; }
.fs-context-menu button { width: 100%; background: none; border: 0; border-radius: 4px; display: flex; justify-content: space-between; gap: 10px; padding: 9px 10px; color: #e1e7ef; font-size: .8rem; text-align: left; cursor: pointer; }
.fs-context-menu button:hover:not(:disabled), .fs-context-menu button:focus-visible { background: #3e4c61; outline: none; }
.fs-context-menu button:disabled { color: #697485; cursor: default; }
.fs-context-menu small { color: #a1afc3; font-size: .7rem; }
.danger { color: #ffabab; border-color: #844545; }
@media (max-width: 1100px) { .fs-shell { grid-template-columns: 170px minmax(0, 1fr); } .fs-table .fs-col-mtime { width: 116px; } .fs-table .fs-col-type { display: none; } .fs-search { width: 140px; } }
@media (max-width: 760px) { .fs-heading { padding: 14px; } .fs-heading p { max-width: 230px; } .fs-shell { display: block; } .fs-tree { display: flex; align-items: center; gap: 5px; max-height: 110px; overflow: auto; border-right: 0; border-bottom: 1px solid #343840; padding: 8px; } .fs-tree-title, .fs-tree .fs-expander, .fs-tree-line[style] { display: none; } .fs-tree-line { flex-shrink: 0; } .fs-tree-name { padding: 7px 10px; } .fs-location { gap: 6px; } .fs-search { width: 100%; } .fs-toolbar { gap: 5px; } .fs-secondary-action { display: none; } .fs-toolbar button { padding: 7px 8px; } .fs-table .fs-col-mtime, .fs-table .fs-col-type { display: none; } .fs-table .fs-col-size { width: 74px; } .fs-table .fs-col-status { width: 70px; } .fs-table .fs-check { width: 27px; padding-left: 6px; } .fs-table td { padding: 12px 4px; } .fs-file-icon { margin-right: 5px; } .fs-help[open] { position: absolute; background: #292e38; padding: 12px; left: 20px; right: 20px; z-index: 3; max-width: none; } .fs-pending { margin: 0 10px 12px; padding: 10px; } .fs-status { flex-wrap: wrap; } }
</style>
