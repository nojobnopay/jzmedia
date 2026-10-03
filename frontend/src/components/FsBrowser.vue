<template>
  <section :id="active ? 'sec-files' : undefined" ref="root" class="fs-browser" tabindex="0" aria-label="文件管理器" @keydown="onKeydown" @pointerdown="closeOutsideMenu">
    <header class="fs-heading">
      <p>单击定位 · 双击打开 · 勾选后批量操作</p>
      <details class="fs-help"><summary>操作说明</summary><p>点击左侧复选框勾选，Shift + 点击复选框连选。方向键定位，空格勾选，Enter 打开。Ctrl / ⌘ + A 全选、C 复制、X 剪切、V 粘贴；F2 改名、Delete 删除仅操作勾选项，Esc 清除。右键菜单会显示操作对象，不改变勾选。Backspace 上级、F5 刷新。快捷键仅在文件管理器内生效。目录仅支持复制、删除空目录；改名或移动目录请使用归档整理。</p></details>
    </header>
    <div v-if="pending" class="fs-pending" role="status"><span><strong>{{ pendingChanges.count || 1 }} 项文件变更待扫描</strong><small>{{ library?.name }} · 扫描后继续核对匹配结果</small></span><JzButton :disabled="hasPendingOperation" @click="scan" type="button" icon="scan">扫描并核对</JzButton></div>
    <div class="fs-shell">
      <nav class="fs-tree" aria-label="视频库目录">
        <span class="fs-tree-title">视频库</span>
        <template v-for="lib in videoLibs" :key="lib.id">
          <div :class="['fs-tree-line', { current: libraryId === Number(lib.id) && !path }]">
            <button v-if="libraryId === Number(lib.id)" class="fs-expander" :aria-label="treeExpanded.has('') ? '收起目录' : '展开目录'" :aria-expanded="treeExpanded.has('')" @click="toggleTree('')"><AppIcon :name="treeExpanded.has('') ? 'chevron-down' : 'chevron-right'" :size="16" /></button><span v-else class="fs-expander"></span>
            <button class="fs-tree-name" :disabled="busy" @click="switchLibrary(lib.id)"><AppIcon name="library" :size="18" /> {{ libraryName(lib.id) }}<small>{{ lib.kind === 'tv' ? '剧集' : '电影' }}</small></button>
          </div>
          <template v-if="libraryId === Number(lib.id)">
            <div v-for="dir in treeRows" :key="dir.rel" :class="['fs-tree-line', { current: path === dir.rel }]" :style="{ paddingLeft: (dir.depth * 13) + 'px' }">
              <button class="fs-expander" :aria-label="(treeExpanded.has(dir.rel) ? '收起 ' : '展开 ') + dir.name" :aria-expanded="treeExpanded.has(dir.rel)" :disabled="treeLoading.has(dir.rel)" @click="toggleTree(dir.rel)"><Spinner v-if="treeLoading.has(dir.rel)" :size="12" /><AppIcon v-else :name="treeExpanded.has(dir.rel) ? 'chevron-down' : 'chevron-right'" :size="16" /></button>
              <button class="fs-tree-name" :title="dir.rel" :disabled="busy" @click="navigate(dir.rel)"><AppIcon name="folder" :size="18" /> {{ dir.name }}</button>
            </div>
          </template>
        </template>
      </nav>
      <div class="fs-main">
        <div class="fs-location">
          <div class="fs-navigation"><JzButton title="返回" aria-label="返回" :disabled="busy || historyIndex <= 0" @click="historyMove(-1)" type="button" icon-only><AppIcon name="back" /></JzButton><JzButton title="前进" aria-label="前进" :disabled="busy || historyIndex >= history.length - 1" @click="historyMove(1)" type="button" icon-only><AppIcon name="forward" /></JzButton><JzButton title="上级目录" aria-label="上级目录" :disabled="busy || !path" @click="up" type="button" icon-only><AppIcon name="arrow-up" /></JzButton><JzButton title="刷新目录" aria-label="刷新目录" :disabled="busy || loading" @click="load(loadError ? loadTarget : path)" type="button" icon-only><AppIcon name="refresh" /></JzButton></div>
          <nav class="fs-crumbs" aria-label="当前位置"><button :disabled="busy" @click="navigate('')">{{ library?.name || '选择视频库' }}</button><template v-for="crumb in crumbs" :key="crumb.rel"><span aria-hidden="true">/</span><button :disabled="busy" @click="navigate(crumb.rel)">{{ crumb.name }}</button></template></nav>
          <input v-model="query" class="fs-search" type="search" aria-label="搜索当前目录" placeholder="搜索当前目录" :disabled="loading || !!loadError || !hasLoaded" />
        </div>
        <div class="fs-toolbar" role="toolbar" aria-label="文件操作">
          <JzButton class="fs-open-action" :disabled="!currentRow || busy || loading || !!loadError" @click="open()" type="button" icon="eye">{{ currentRow?.isDir ? '打开文件夹' : '预览' }}</JzButton>
          <JzButton :disabled="!writable || busy || loading || !!loadError" @click="beginMkdir" type="button" icon="folder">新建文件夹</JzButton>
          <JzButton class="fs-secondary-action" :disabled="!selection.length || busy || loading || !!loadError" @click="copy('copy')" type="button" icon="copy">复制</JzButton>
          <JzButton class="fs-secondary-action" :disabled="!canCut" :title="selectedRows.some(r => r.isDir) ? '目录移动请使用归档整理' : '剪切选中文件'" @click="copy('cut')" type="button" icon="cut">剪切</JzButton>
          <JzButton :disabled="!canPaste" @click="paste" type="button" icon="paste">粘贴{{ clipboard.mode === 'cut' ? '（移动）' : '' }}</JzButton>
          <JzButton class="fs-secondary-action" :disabled="!canRename" :title="one?.isDir ? '目录改名请使用归档整理' : '重命名（F2）'" @click="beginRename()" type="button" icon="edit">重命名</JzButton>
          <JzButton class="fs-secondary-action" :disabled="!writable || !selection.length || busy || loading || !!loadError" @click="beginDelete()" type="button" icon="delete">删除…</JzButton>
          <JzButton ref="moreButton" aria-label="更多文件操作" :aria-expanded="!!menu" @click="openMore" type="button" icon="more">更多</JzButton>
          <span v-if="!writable && hasLoaded && !loading && !loadError && libraryId" class="fs-readonly">只读</span>
        </div>
        <p v-if="message" class="fs-message" role="status">{{ message }}</p>
        <div v-if="prompt" ref="promptEl" class="fs-prompt" role="region" aria-label="文件操作确认">
          <div class="fs-prompt-heading"><strong>{{ promptTitle }}</strong><JzButton variant="ghost" :disabled="busy" aria-label="取消操作" @click="prompt = null" type="button" icon-only><AppIcon name="close" /></JzButton></div>
          <p class="fs-target">{{ library?.name }} / {{ (prompt.to_dir ?? prompt.path ?? path) || '根目录' }}</p>
          <form v-if="prompt.type === 'rename' || prompt.type === 'mkdir'" @submit.prevent="submitName">
            <label :for="inputId">{{ prompt.type === 'rename' ? '新文件名' : '文件夹名称' }}</label><input :id="inputId" ref="nameInput" v-model="inputName" :disabled="busy" autocomplete="off" />
            <template v-if="prompt.plans"><p v-for="plan in prompt.plans" :key="plan.from" class="fs-plan">{{ plan.from }} → {{ plan.to }}<strong v-if="plan.status !== 'planned'"> · {{ statusText(plan.status) }}</strong></p><template v-for="plan in prompt.plans" :key="'followers-' + plan.from"><p v-for="follower in plan.followers || []" :key="follower.from" class="fs-plan fs-follower">关联文件：{{ follower.from }} → {{ follower.to }}<span v-if="follower.status !== 'planned'"> · {{ statusText(follower.status) }}，保留原文件</span></p></template></template>
            <div class="fs-confirm-actions"><JzButton :disabled="busy || !inputName.trim() || (prompt.plans && (!prompt.plans.length || !prompt.plans.every(p => p.status === 'planned')))" type="submit" icon="eye">{{ prompt.type === 'mkdir' ? '创建文件夹' : prompt.plans ? '确认改名' : '预览改名' }}</JzButton><JzButton type="button" :disabled="busy" @click="prompt = null">取消</JzButton></div>
          </form>
          <template v-else-if="prompt.type === 'delete'">
            <p class="fs-warning">文件会被物理删除，无法恢复。正片的入库记录和播放进度也会清理；非空目录不会删除。</p>
            <ul class="fs-plan-list"><li v-for="plan in prompt.plans" :key="plan.rel"><strong v-if="plan.requires_confirm">正片 · </strong>{{ plan.title || plan.name || plan.rel }}<small>{{ plan.rel }}</small><span v-if="plan.version_count > 1"> · {{ plan.version_count }} 个版本中的 1 个</span><span v-if="plan.status && plan.status !== 'planned_rmdir'"> · {{ statusText(plan.status) }}</span></li></ul>
            <div class="fs-confirm-actions"><JzButton class="danger" :disabled="busy || !prompt.plans.some(p => !p.status || p.status === 'planned_rmdir')" @click="confirmDelete" type="button" variant="danger">确认永久删除</JzButton><JzButton :disabled="busy" @click="prompt = null" type="button">取消</JzButton></div>
          </template>
          <template v-else-if="prompt.type === 'copy'">
            <p>共 {{ prompt.preview.total }} 项 / {{ prompt.preview.files }} 个文件 / {{ fmtBytes(prompt.preview.bytes) }}。同名项目自动添加“(副本)”，保留已有文件。</p>
            <p v-if="prompt.preview.conflicts?.length">{{ prompt.preview.conflicts.length }} 项同名冲突将自动改名。</p>
            <div class="fs-confirm-actions"><JzButton :disabled="busy" @click="confirmPaste" type="button">确认复制到此目录</JzButton><JzButton :disabled="busy" @click="prompt = null" type="button">取消</JzButton></div>
          </template>
          <template v-else-if="prompt.type === 'move'">
            <ul class="fs-plan-list"><li v-for="plan in prompt.plans" :key="plan.from">{{ plan.from }} → {{ plan.to || prompt.to_dir || '根目录' }}<strong v-if="plan.status !== 'planned'"> · {{ statusText(plan.status) }}</strong><small v-for="follower in plan.followers || []" :key="follower.from">关联文件：{{ follower.from }} → {{ follower.to }}{{ follower.status !== 'planned' ? ' · 冲突，保留原文件' : '' }}</small></li></ul>
            <p>匹配结果保持不变；完成后扫描核对。目标已存在的项目会跳过。</p>
            <div class="fs-confirm-actions"><JzButton :disabled="busy || !prompt.plans.some(p => p.status === 'planned')" @click="confirmPaste" type="button">确认移动</JzButton><JzButton :disabled="busy" @click="prompt = null" type="button">取消</JzButton></div>
          </template>
        </div>
        <div class="fs-table-area">
        <div class="fs-table-wrap" :aria-busy="loading" @contextmenu.prevent="openContext($event)" @click.self="resetSelection(); root?.focus()">
          <table class="fs-table" :inert="loading || !!loadError || !hasLoaded" aria-label="当前目录文件" aria-multiselectable="true">
            <thead><tr><th class="fs-check"><label class="fs-check-target"><input type="checkbox" aria-label="全选当前目录" :checked="!!rows.length && selection.length === rows.length" :indeterminate="selection.length > 0 && selection.length < rows.length" :disabled="loading || !!loadError || !hasLoaded || !rows.length" @change="$event.target.checked ? selectAll() : clearSelection()" /></label></th><th v-for="col in columns" :key="col.key" :class="'fs-col-' + col.key" :aria-sort="sort === col.key ? (direction === 1 ? 'ascending' : 'descending') : 'none'"><button @click="setSort(col.key)">{{ col.label }}<AppIcon v-if="sort === col.key" :name="direction === 1 ? 'sort-asc' : 'sort-desc'" :size="16" /></button><span v-if="col.key === 'name'" class="fs-mobile-sort"><select :value="sort" aria-label="文件排序" @change="setSort($event.target.value)"><option v-for="option in columns" :key="option.key" :value="option.key">按{{ option.label }}</option></select><button :aria-label="direction === 1 ? '切换为降序' : '切换为升序'" @click="setSort(sort)"><AppIcon :name="direction === 1 ? 'sort-asc' : 'sort-desc'" :size="18" /></button></span></th></tr></thead>
            <tbody><tr v-for="row in rows" :key="row.rel" class="fs-row" :class="{ selected: selection.includes(row.rel), focused: focused === row.rel, cut: clipboard.mode === 'cut' && clipboard.library_id === libraryId && clipboard.rels.includes(row.rel) }" :data-fs-rel="row.rel" tabindex="-1" :aria-current="focused === row.rel ? 'true' : undefined" :aria-selected="selection.includes(row.rel)" @click="rowClick($event, row)" @dblclick="open(row)" @contextmenu.stop.prevent="openContext($event, row)">
              <td class="fs-check" @click.stop @dblclick.stop><label class="fs-check-target"><input type="checkbox" :aria-label="'选择 ' + row.name" :checked="selection.includes(row.rel)" :disabled="loading || !!loadError || !hasLoaded" @focus="focusRow(row)" @click="checkboxClick($event, row)" /></label></td>
              <td class="fs-name" :title="row.name"><div class="fs-file-caption"><AppIcon :name="row.isDir ? 'folder' : 'file'" :size="18" class="fs-file-icon" :class="{ folder: row.isDir }" /><div><span class="fs-file-label">{{ row.name }}</span><small class="fs-mobile-meta">{{ row.isDir ? '文件夹' : fmtBytes(row.size) + ' · ' + fsType(row) }}<template v-if="fsMatchStatus(row) !== '—'"> · {{ fsMatchStatus(row) }}</template></small></div></div></td>
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
          <strong>目录加载失败</strong><span>{{ library?.name }} / {{ loadTarget || '根目录' }}</span><p>{{ loadError }}</p><JzButton :disabled="busy" @click="retryLoad" type="button" icon="refresh">重试加载目录</JzButton>
        </div>
        <div v-else-if="loadState === 'unselected'" class="fs-directory-state"><p>请先选择一个视频库</p></div>
        </div>
        <footer class="fs-status fs-selection-summary" role="status"><span v-if="loadState === 'loading'">正在加载目录…</span><span v-else-if="loadState === 'error'">目录未能加载</span><span v-else-if="loadState !== 'ready'">等待读取目录</span><span v-else>{{ rows.length }} 项{{ query ? `（共 ${dirs.length + files.length} 项）` : '' }}<strong v-if="selection.length"> · 已勾选 {{ selection.length }} 项</strong></span><JzButton variant="ghost" size="compact" v-if="selection.length" @click="clearSelection" type="button">清除勾选</JzButton><span v-if="clipboard.rels.length">{{ clipboard.mode === 'cut' ? '已剪切' : '已复制' }} {{ clipboard.rels.length }} 项{{ clipboard.library_id !== libraryId ? '（其他视频库）' : '' }}</span></footer>
      </div>
    </div>
    <div v-if="jobs.length" class="fs-tasks" aria-label="文件任务"><div v-for="job in jobs" :key="job.job_id" class="fs-task"><span>{{ jobText(job) }}<small>目标：{{ libraryName(job.library_id) }} / {{ job.to_dir || '根目录' }}</small></span><progress :value="jobProgress(job)" max="100" :aria-label="jobText(job)"></progress><JzButton v-if="jobRunning(job)" :disabled="job.state === 'cancelled'" @click="cancelCopy(job)" type="button" icon="copy">{{ job.state === 'cancelled' ? '正在取消…' : '取消复制' }}</JzButton><JzButton v-else @click="jobs = jobs.filter(j => j.job_id !== job.job_id)" type="button">收起</JzButton><details v-if="jobIssues(job).length" class="fs-task-issues"><summary>查看需核对项目（{{ jobIssues(job).length }}）</summary><ul><li v-for="item in jobIssues(job)" :key="item.from">{{ item.from }} → {{ item.to }}<small>{{ jobIssueText(item) }}</small></li></ul></details></div></div>
    <FilePreviewHost :file="previewFile" :active="active" @close="closeFilePreview" />
    <Teleport to="body"><div v-if="menu" ref="menuEl" class="fs-context-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" role="menu" aria-label="文件菜单" @keydown="onMenuKeydown" @contextmenu.prevent><div class="fs-menu-target">{{ menu.targetLabel }}</div><button v-for="item in menuItems" :key="item.id" role="menuitem" :disabled="item.disabled" :title="item.hint || ''" @click="runMenu(item)"><span><AppIcon :name="item.icon" :size="18" />{{ item.label }}</span><small>{{ item.shortcut }}</small></button></div></Teleport>
  </section>
</template>

<script setup>
import JzButton from './JzButton.vue'

import AppIcon from './AppIcon.vue'
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
  selection, focused, clipboard, prompt, previewFile, inputName, jobs, history, historyIndex, treeExpanded, treeLoading, rows, one, currentRow, selectedRows,
  canRename, canCut, canPaste, pending, hasRunningTask, hasPendingOperation, treeRows,
  scan, switchLibrary, navigate, load, retryLoad, historyMove, up, toggleTree, selectAll, focusRow, toggleSelection, clearSelection, moveFocus, open, viewDetails, closePreview, setSort, copy, copyPath,
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
const menuRows = computed(() => rows.value.filter(row => menu.value?.rels.includes(row.rel)))
const menuOne = computed(() => menuRows.value.length === 1 ? menuRows.value[0] : null)
const menuBlocked = computed(() => busy.value || loading.value || !!loadError.value)
const menuItems = computed(() => [
  { id: 'open', icon: 'eye', label: menuOne.value?.isDir ? '打开文件夹' : '预览', disabled: !menuOne.value || menuBlocked.value, shortcut: 'Enter', run: () => open(menuOne.value) },
  { id: 'details', icon: 'info', label: '查看媒体详情', disabled: !menuOne.value || menuOne.value.isDir || (!menuOne.value.movie_id && !menuOne.value.show_id) || menuBlocked.value, run: () => viewDetails(menuOne.value) },
  { id: 'cut', icon: 'cut', label: '剪切', disabled: !writable.value || !menuRows.value.length || menuRows.value.some(r => r.isDir) || menuBlocked.value, hint: menuRows.value.some(r => r.isDir) ? '目录移动请使用归档整理' : '', shortcut: 'Ctrl+X', run: () => copy('cut', menu.value.rels) },
  { id: 'copy', icon: 'copy', label: '复制', disabled: !menuRows.value.length || menuBlocked.value, shortcut: 'Ctrl+C', run: () => copy('copy', menu.value.rels) },
  { id: 'paste', icon: 'paste', label: clipboard.value.mode === 'cut' ? '粘贴（移动）' : '粘贴', disabled: !canPaste.value, shortcut: 'Ctrl+V', run: paste },
  { id: 'rename', icon: 'edit', label: '重命名', disabled: !writable.value || !menuOne.value || menuOne.value.isDir || menuBlocked.value, hint: menuOne.value?.isDir ? '目录改名请使用归档整理' : '', shortcut: 'F2', run: () => beginRename(menuOne.value) },
  { id: 'delete', icon: 'delete', label: '删除…', disabled: !writable.value || !menuRows.value.length || menuBlocked.value, shortcut: 'Delete', run: () => beginDelete(menu.value.rels) },
  { id: 'mkdir', icon: 'folder', label: '新建文件夹', disabled: !writable.value || busy.value || loading.value || !!loadError.value, run: beginMkdir },
  { id: 'path', icon: 'link', label: '复制库内路径', disabled: libraryId.value == null, run: () => copyPath(menu.value.rels) },
  { id: 'refresh', icon: 'refresh', label: '刷新', disabled: busy.value || loading.value, shortcut: 'F5', run: () => load(loadError.value ? loadTarget.value : path.value) },
].filter(item => menuRows.value.length || ['paste', 'mkdir', 'path', 'refresh'].includes(item.id)))
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
function rowClick(event, row) { if (loading.value) return; focusRow(row); event.currentTarget.focus() }
function checkboxClick(event, row) {
  toggleSelection(row, event)
  // Shift ranges can keep an already checked item; restore the native click's toggle.
  event.currentTarget.checked = selection.value.includes(row.rel)
}
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
async function showMenu(x, y, row = null, background = false) {
  const checked = !background && (!row || selection.value.includes(row.rel))
  const rels = checked ? [...selection.value] : row ? [row.rel] : []
  const targetLabel = checked && rels.length ? `已勾选 ${rels.length} 项` : row?.name || '当前目录'
  menu.value = { x: Math.max(8, Math.min(x, window.innerWidth - 230)), y: Math.max(8, y), rels, targetLabel }
  await nextTick()
  if (!menu.value || !menuEl.value) return
  const rect = menuEl.value.getBoundingClientRect()
  menu.value.y = Math.max(8, Math.min(menu.value.y, window.innerHeight - rect.height - 8))
  menuEl.value.querySelector('button:not(:disabled)')?.focus()
}
function openContext(event, row = null) {
  if (row) focusRow(row)
  showMenu(event.clientX, event.clientY, row, !row)
}
function openMore() {
  if (menu.value) { menu.value = null; return }
  const rect = moreButton.value.el.getBoundingClientRect()
  showMenu(rect.left, rect.bottom + 4)
}
function closeOutsideMenu(event) { if (menu.value && !menuEl.value?.contains(event.target) && !moreButton.value?.el?.contains(event.target)) menu.value = null }
function runMenu(item) { if (item.disabled) return; item.run(); menu.value = null; root.value?.focus() }
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
  else if (event.key === 'Escape') { handled(); clearSelection() }
  else if (event.key === ' ' && event.target.matches('.fs-row')) { handled(); if (currentRow.value) toggleSelection(currentRow.value, event) }
  else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { handled(); moveFocus(event.key === 'ArrowDown' ? 1 : -1, event); focusCurrent() }
  else if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { handled(); const rect = event.target.getBoundingClientRect(); showMenu(rect.left, rect.bottom, currentRow.value) }
}
watch(prompt, async value => { if (value) { await nextTick(); nameInput.value?.focus(); promptEl.value?.scrollIntoView?.({ block: 'nearest' }) } })
watch(() => props.active, active => { if (!active) menu.value = null })
watch(libraryId, () => { menu.value = null })
onMounted(() => window.addEventListener('pointerdown', closeOutsideMenu))
onUnmounted(() => window.removeEventListener('pointerdown', closeOutsideMenu))
defineExpose({ closePreview: closeFilePreview, refresh: () => load(loadError.value ? loadTarget.value : path.value), hasRunningTask: () => hasRunningTask.value, hasPendingOperation: () => hasPendingOperation.value, currentLibraryId: () => libraryId.value })
</script>

<style scoped>
.fs-browser { --jz-control-current: var(--jz-control-compact); color: var(--jz-text); background: var(--jz-surface); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); overflow: hidden; outline: none; }
.fs-browser:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 2px; }
.fs-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding: 12px 16px; }
.fs-heading p { color: var(--jz-text-dim); margin: 0; font-size: .82rem; line-height: 1.6; }
.fs-help { max-width: 420px; font-size: .8rem; color: var(--jz-text-dim); }
.fs-help summary { cursor: pointer; white-space: nowrap; }
.fs-help p { padding-top: 8px; }
.fs-pending { margin: 0 16px 14px; padding: 12px 14px; background: var(--jz-warn-soft); border: 1px solid var(--jz-warn-border); border-radius: var(--jz-radius-m); display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: .87rem; }
.fs-pending small { display: block; margin-top: 4px; color: var(--jz-text-dim); }
.fs-shell { display: grid; grid-template-columns: 210px minmax(0, 1fr); border-top: 1px solid var(--jz-border); }
.fs-tree { display: block; background: var(--jz-bg); border-right: 1px solid var(--jz-border); padding: 14px 8px; overflow: auto; max-height: 680px; }
.fs-tree-title { display: block; margin: 0 8px 12px; font-size: .8rem; color: var(--jz-text-dim); font-weight: 600; }
.fs-tree-line { display: flex; align-items: center; min-height: 36px; border-radius: var(--jz-radius-s); }
.fs-tree-line.current { background: var(--jz-selected); }
.fs-tree-line .fs-expander { width: 22px; min-width: 22px; padding: 4px 2px; border: 0; background: none; color: var(--jz-text-dim); }
.fs-tree-name { display: flex; align-items: center; gap: var(--jz-gap-xs); background: none; border: 0; border-radius: 0; padding: 8px 3px; color: var(--jz-text); font-size: .82rem; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; flex: 1; }
.fs-tree-name small { margin-left: 8px; font-size: .67rem; color: var(--jz-text-faint); }
.fs-main { min-width: 0; display: flex; flex-direction: column; }
.fs-location { padding: 10px 12px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; border-bottom: 1px solid var(--jz-border); }
.fs-navigation { display: flex; gap: 3px; }
.fs-navigation button { width: var(--jz-control-compact); display: inline-flex; justify-content: center; align-items: center; }
.fs-crumbs { padding: 0; background: transparent; flex: 1; min-width: 140px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px; color: var(--jz-text-faint); }
.fs-crumbs button { background: transparent; border: 0; padding: 3px 4px; color: var(--jz-text-dim); font-size: .8rem; overflow-wrap: anywhere; }
.fs-search { width: 166px; min-width: 0; font-size: .8rem; padding: 7px 9px; }
.fs-open-action { min-width: 10em; }
.fs-toolbar { display: flex; gap: 6px; flex-wrap: wrap; padding: 10px 12px; align-items: center; }
.fs-readonly { margin-left: auto; font-size: .75rem; color: var(--jz-warn); }
.fs-selection-summary strong { color: var(--jz-text); }
.fs-selection-summary button { margin-left: auto; }
.fs-message { margin: 0; padding: 9px 14px; color: var(--jz-text-dim); background: var(--jz-info-soft); font-size: .8rem; overflow-wrap: anywhere; }
.fs-prompt { border: 1px solid var(--jz-border-strong); background: var(--jz-surface); margin: 10px 12px; border-radius: var(--jz-radius-m); padding: 12px; font-size: .83rem; }
.fs-prompt-heading { display: flex; justify-content: space-between; align-items: center; }
.fs-target { color: var(--jz-text-dim); font-size: .76rem; overflow-wrap: anywhere; }
.fs-prompt label { display: block; margin: 10px 0 6px; }
.fs-prompt input { box-sizing: border-box; width: 100%; max-width: 540px; }
.fs-confirm-actions { display: flex; gap: 8px; margin-top: 12px; }
.fs-warning { color: var(--jz-warn); line-height: 1.6; }
.fs-plan, .fs-plan-list { overflow-wrap: anywhere; line-height: 1.7; }
.fs-follower { color: var(--jz-text-dim); font-size: .78rem; }
.fs-plan-list { max-height: 200px; overflow: auto; padding-left: 22px; }
.fs-plan-list small { display: block; color: var(--jz-text-dim); }
.fs-table-area { position: relative; display: flex; flex-direction: column; min-height: 260px; flex: 1; }
.fs-directory-state { position: absolute; inset: 0; z-index: 2; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; padding: 24px; box-sizing: border-box; background: var(--jz-surface); color: var(--jz-text-dim); text-align: center; font-size: .85rem; }
.fs-directory-state strong { color: var(--jz-text); font-size: .96rem; }
.fs-directory-state span, .fs-directory-state p { overflow-wrap: anywhere; max-width: 100%; margin: 0; }
.fs-directory-state progress { width: min(240px, 80%); height: 6px; accent-color: var(--jz-accent); }
.fs-directory-error p { color: var(--jz-warn); max-height: 100px; overflow: auto; }
.fs-table-wrap { overflow: auto; position: relative; min-height: 260px; max-height: 540px; flex: 1; border-top: 1px solid var(--jz-border); }
.fs-table { border-collapse: collapse; width: 100%; table-layout: fixed; font-size: .79rem; }
.fs-table th { text-align: left; position: sticky; top: 0; background: var(--jz-surface-2); z-index: 1; border-bottom: 1px solid var(--jz-border-strong); font-weight: 500; }
.fs-table th button { display: inline-flex; align-items: center; gap: var(--jz-gap-xs); border: 0; background: none; padding: 10px 6px; color: var(--jz-text-dim); font: inherit; white-space: nowrap; }
.fs-table td { padding: 10px 6px; border-bottom: 1px solid var(--jz-border); color: var(--jz-text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fs-table .fs-check { width: 44px; padding: 0; }
.fs-check-target { display: flex; align-items: center; justify-content: center; width: 44px; min-height: 40px; margin: 0; cursor: pointer; user-select: none; }
.fs-check input { width: 16px; height: 16px; margin: 0; accent-color: var(--jz-accent); cursor: pointer; }
.fs-table .fs-col-name { min-width: 160px; }
.fs-table .fs-col-size { width: 82px; }
.fs-table .fs-col-mtime { width: 142px; }
.fs-table .fs-col-type { width: 60px; }
.fs-table .fs-col-status { width: 86px; }
.fs-table td.fs-name { color: var(--jz-text); }
.fs-file-caption { display: flex; gap: 10px; align-items: center; min-width: 0; }
.fs-file-caption > div { min-width: 0; flex: 1; }
.fs-file-label { display: block; overflow: hidden; text-overflow: ellipsis; }
.fs-file-icon { color: var(--jz-link); }
.fs-mobile-meta, .fs-mobile-sort { display: none; }
.fs-file-icon.folder { color: var(--jz-warn); }
.fs-row { cursor: default; user-select: none; outline: none; }
.fs-row:hover { background: var(--jz-surface-2); }
.fs-row.focused { background: var(--jz-surface-3); }
.fs-row.selected { background: var(--jz-selected); }
.fs-row:focus-visible { outline: 1px solid var(--jz-link); outline-offset: -1px; }
.fs-row.cut { opacity: .5; }
.fs-unmatched { color: var(--jz-warn); }
.fs-unregistered { color: var(--jz-blue-chip); }
.fs-empty { text-align: center; padding: 64px 16px; font-size: .85rem; color: var(--jz-text-faint); }
.fs-status { padding: 4px 14px; min-height: var(--jz-control-current); display: flex; align-items: center; gap: 12px; color: var(--jz-text-dim); font-size: .75rem; border-top: 1px solid var(--jz-border); }
.fs-tasks { padding: 12px 16px; border-top: 1px solid var(--jz-border); background: var(--jz-surface); }
.fs-task { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 6px 0; font-size: .8rem; }
.fs-task span { flex: 1; min-width: 150px; }
.fs-task small { display: block; color: var(--jz-text-dim); margin-top: 4px; }
.fs-task-issues { flex-basis: 100%; color: var(--jz-warn); overflow-wrap: anywhere; }
.fs-task-issues summary { cursor: pointer; }
.fs-task-issues ul { max-height: 200px; overflow: auto; padding-left: 20px; }
.fs-task progress { height: 7px; width: 140px; accent-color: var(--jz-accent); }
.fs-context-menu { position: fixed; z-index: var(--jz-z-menu); width: 220px; box-sizing: border-box; max-height: calc(100vh - 16px); overflow-y: auto; padding: 6px; background: var(--jz-surface-2); border: 1px solid var(--jz-border-strong); box-shadow: var(--jz-shadow-menu); border-radius: var(--jz-radius-m); }
.fs-context-menu button { width: 100%; background: none; border: 0; border-radius: 4px; display: flex; justify-content: space-between; gap: 10px; padding: 9px 10px; color: var(--jz-text); font-size: .8rem; text-align: left; cursor: pointer; }
.fs-context-menu button:hover:not(:disabled), .fs-context-menu button:focus-visible { background: var(--jz-surface-3); outline: none; }
.fs-context-menu button:disabled { color: var(--jz-text-faint); cursor: default; }
.fs-context-menu button > span { display: inline-flex; align-items: center; gap: var(--jz-gap-s); }
.fs-context-menu small { color: var(--jz-text-dim); font-size: .7rem; }
.fs-menu-target { padding: 6px 10px 10px; margin-bottom: 4px; border-bottom: 1px solid var(--jz-border); color: var(--jz-text-dim); font-size: .75rem; overflow-wrap: anywhere; }
@media (max-width: 1100px) { .fs-shell { grid-template-columns: 170px minmax(0, 1fr); } .fs-table .fs-col-mtime { width: 116px; } .fs-table .fs-col-type { display: none; } .fs-search { width: 140px; } }
@media (max-width: 760px) { .fs-heading { padding: 14px; } .fs-heading p { max-width: 230px; } .fs-shell { display: block; } .fs-tree { display: flex; align-items: center; gap: 5px; max-height: 110px; overflow: auto; border-right: 0; border-bottom: 1px solid var(--jz-border); padding: 8px; } .fs-tree-title, .fs-tree .fs-expander, .fs-tree-line[style] { display: none; } .fs-tree-line { flex-shrink: 0; } .fs-tree-name { display: flex; align-items: center; gap: var(--jz-gap-xs); padding: 7px 10px; } .fs-location { gap: 6px; } .fs-search { width: 100%; } .fs-toolbar { gap: 5px; } .fs-secondary-action { display: none; } .fs-toolbar button { padding: 7px 8px; } .fs-table .fs-col-mtime, .fs-table .fs-col-type, .fs-table .fs-col-size, .fs-table .fs-col-status { display: none; } .fs-table .fs-check { width: 44px; padding: 0; } .fs-table td { padding: 12px 4px; } .fs-file-caption { gap: 8px; } .fs-file-label { white-space: normal; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; } .fs-mobile-meta { display: block; margin-top: 4px; font-size: .75rem; color: var(--jz-text-dim); } .fs-col-name > button { display: none; } .fs-mobile-sort { display: flex; align-items: center; justify-content: space-between; padding: 4px 8px 4px 0; gap: 8px; } .fs-mobile-sort select { flex: 1; min-width: 0; border: 0; background: transparent; } .fs-help[open] { position: absolute; background: var(--jz-surface-2); padding: 12px; left: 20px; right: 20px; z-index: 3; max-width: none; } .fs-pending { margin: 0 10px 12px; padding: 10px; } .fs-status { flex-wrap: wrap; } }
.fs-table .fs-col-size, .fs-table .fs-col-mtime, .fs-status { font-variant-numeric: tabular-nums; }
@media (max-width: 700px), (pointer: coarse) {
  .fs-crumbs { min-width: 0; }
  .fs-browser { --jz-control-current: var(--jz-touch-target); }
  .fs-navigation button { width: var(--jz-touch-target); }
  .fs-toolbar button, .fs-context-menu button, .fs-tree-name { display: flex; align-items: center; gap: var(--jz-gap-xs); min-height: var(--jz-touch-target); }
  .fs-check-target { min-height: var(--jz-touch-target); }
}
@media (max-width: 700px), (pointer: coarse) {
  .fs-tree-line .fs-expander, .fs-mobile-sort button { min-width: var(--jz-touch-target); min-height: var(--jz-touch-target); }
  .fs-crumbs button, .fs-tree-name, .fs-context-menu button, .fs-check-target { min-height: var(--jz-touch-target); }
}
</style>
