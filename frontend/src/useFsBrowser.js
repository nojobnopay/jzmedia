import { computed, ref, watch, onUnmounted } from 'vue'
import { usePolling } from './usePolling.js'
import { fmtBytes } from './format.js'
import { fsBlobUrl } from './filePreview.js'
import { createFsDirectoryLoader, fsCopyIssues, fsJobRunning, fsListUrl, fsPasteSnapshot, fsSelect, fsVisibleRows } from './fsBrowser.js'

export function useFsBrowser(props, emit, api, router) {
  const libraryId = computed(() => props.initialLibId == null ? null : Number(props.initialLibId))
  const library = computed(() => props.videoLibs.find(l => Number(l.id) === libraryId.value))
  const path = ref('')
  const dirs = ref([])
  const files = ref([])
  const crumbs = ref([])
  const writable = ref(false)
  const loading = ref(false)
  const hasLoaded = ref(false)
  const loadError = ref('')
  const loadTarget = ref('')
  const loadState = computed(() => loading.value ? 'loading' : loadError.value ? 'error' : hasLoaded.value ? 'ready' : libraryId.value == null ? 'unselected' : 'waiting')
  const loadedCount = ref(0)
  const totalCount = ref(0)
  const message = ref('')
  const busy = ref(false)
  const query = ref('')
  const sort = ref('name')
  const direction = ref(1)
  const selection = ref([])
  const anchor = ref(null)
  const focused = ref(null)
  const clipboard = ref({ mode: 'copy', library_id: null, rels: [] })
  const prompt = ref(null)
  const previewFile = ref(null)
  const inputName = ref('')
  const jobs = ref([])
  const history = ref([''])
  const historyIndex = ref(0)
  const treeCache = ref({})
  const treeExpanded = ref(new Set(['']))
  const treeLoading = ref(new Set())
  const loader = createFsDirectoryLoader(api)
  let disposed = false
  let loadGeneration = 0
  let refreshQueued = null
  let historyGeneration = 0
  const rows = computed(() => fsVisibleRows(dirs.value, files.value, query.value, sort.value, direction.value))
  const selectedRows = computed(() => rows.value.filter(r => selection.value.includes(r.rel)))
  const one = computed(() => selectedRows.value.length === 1 ? selectedRows.value[0] : null)
  const canRename = computed(() => writable.value && one.value && !one.value.isDir && !busy.value && !loading.value && !loadError.value)
  const canCut = computed(() => writable.value && selectedRows.value.length && selectedRows.value.every(r => !r.isDir) && !busy.value && !loading.value && !loadError.value)
  const canPaste = computed(() => writable.value && clipboard.value.rels.length && clipboard.value.library_id === libraryId.value && !busy.value && !loading.value && !loadError.value)
  const pending = computed(() => props.pendingChanges?.pending === true && Number(props.pendingChanges.library_id) === libraryId.value)
  const hasAnyRunningTask = computed(() => jobs.value.some(fsJobRunning))
  const hasRunningTask = computed(() => jobs.value.some(j => Number(j.library_id) === libraryId.value && fsJobRunning(j)))
  const hasPendingOperation = computed(() => busy.value || hasRunningTask.value)
  const treeRows = computed(() => {
    const result = []
    function walk(parent, depth) {
      if (!treeExpanded.value.has(parent)) return
      for (const d of treeCache.value[parent] || []) {
        result.push({ ...d, depth })
        walk(d.rel, depth + 1)
      }
    }
    walk('', 1)
    return result
  })

  function changed(id, operation, count = 1) {
    emit('changed', { library_id: Number(id), operation, count })
  }
  function scan() { if (libraryId.value != null) emit('scan', { library_id: libraryId.value }) }
  function switchLibrary(id) {
    if (busy.value) return
    if (Number(id) !== libraryId.value) emit('library-change', { library_id: Number(id) })
    else navigate('')
  }
  function resetSelection() { selection.value = []; anchor.value = null; focused.value = null }
  async function load(target = path.value, { record = true, clearMessage = true } = {}) {
    if (libraryId.value == null) return
    const id = libraryId.value
    const generation = ++loadGeneration
    loading.value = true
    loadError.value = ''
    loadTarget.value = target || ''
    if (clearMessage) message.value = ''
    loadedCount.value = 0
    totalCount.value = 0
    if (!previewFile.value) resetSelection()
    try {
      await loader.load(id, target, data => {
        if (disposed || id !== libraryId.value) return
        hasLoaded.value = true
        path.value = data.path || ''
        dirs.value = data.dirs || []
        files.value = data.files || []
        crumbs.value = data.crumbs || []
        writable.value = data.fs_writable !== false
        treeCache.value = { ...treeCache.value, [path.value]: dirs.value }
        const ancestors = ['', ...crumbs.value.map(c => c.rel)]
        treeExpanded.value = new Set([...treeExpanded.value, ...ancestors])
        if (record && history.value[historyIndex.value] !== path.value) {
          history.value = [...history.value.slice(0, historyIndex.value + 1), path.value]
          historyIndex.value = history.value.length - 1
        }
      }, (done, total) => { loadedCount.value = done; totalCount.value = total })
    } catch (e) {
      if (generation === loadGeneration && !disposed) loadError.value = e.message || '目录读取失败，请重试'
    } finally {
      if (generation === loadGeneration && !disposed) {
        loading.value = false
        if (refreshQueued === libraryId.value && !loadError.value) {
          const queued = refreshQueued
          refreshQueued = null
          await refreshAfter(queued)
        }
      }
    }
  }
  function retryLoad() { if (!busy.value && !loading.value) return load(loadTarget.value) }
  function navigate(target) {
    if (busy.value) return
    historyGeneration++
    prompt.value = null
    closePreview()
    query.value = ''
    return load(target)
  }
  async function historyMove(delta) {
    const next = historyIndex.value + delta
    if (next < 0 || next >= history.value.length || busy.value) return
    const previous = historyIndex.value
    historyIndex.value = next
    query.value = ''
    prompt.value = null
    const generation = ++historyGeneration
    await load(history.value[next], { record: false })
    if (generation !== historyGeneration) return
    if (path.value !== history.value[next]) historyIndex.value = previous
  }
  function up() { if (path.value) navigate(path.value.split('/').slice(0, -1).join('/')) }
  async function toggleTree(rel) {
    if (treeExpanded.value.has(rel)) { treeExpanded.value = new Set([...treeExpanded.value].filter(p => p !== rel)); return }
    treeExpanded.value = new Set([...treeExpanded.value, rel])
    if (treeCache.value[rel]) return
    const id = libraryId.value
    treeLoading.value = new Set([...treeLoading.value, rel])
    try {
      const data = await api(fsListUrl(id, rel, 0, 1))
      if (!disposed && id === libraryId.value) treeCache.value = { ...treeCache.value, [rel]: data.dirs || [] }
    } catch (e) { if (!disposed && id === libraryId.value) message.value = '目录树加载失败：' + e.message }
    finally { if (id === libraryId.value) treeLoading.value = new Set([...treeLoading.value].filter(p => p !== rel)) }
  }
  function select(row, event = {}) {
    const next = fsSelect(rows.value, selection.value, anchor.value, row.rel, event)
    selection.value = next.selected
    anchor.value = next.anchor
    focused.value = row.rel
  }
  function selectAll() { selection.value = rows.value.map(r => r.rel) }
  function moveFocus(delta, event) {
    if (!rows.value.length) return
    const index = rows.value.findIndex(r => r.rel === focused.value)
    const next = index < 0 ? 0 : Math.max(0, Math.min(rows.value.length - 1, index + delta))
    select(rows.value[next], event)
  }
  function open(row = one.value) {
    if (!row || busy.value || loading.value || loadError.value) return
    if (row.isDir) navigate(row.rel)
    else previewFile.value = { ...row, library_id: libraryId.value, url: fsBlobUrl(libraryId.value, row.rel) }
  }
  function viewDetails(row = one.value) {
    if (!row || busy.value || loading.value || loadError.value) return
    if (row.show_id) router.push('/tv/' + row.show_id)
    else if (row.movie_id) router.push('/m/' + row.movie_id)
  }
  function closePreview() { previewFile.value = null }
  function setSort(key) {
    if (sort.value === key) direction.value *= -1
    else { sort.value = key; direction.value = 1 }
  }
  function copy(mode = 'copy') {
    if (!selection.value.length || busy.value || loading.value || loadError.value) return
    if (mode === 'cut' && !canCut.value) { message.value = '目录不支持剪切移动，请使用目录整理'; return }
    clipboard.value = { mode, library_id: libraryId.value, rels: [...selection.value] }
    message.value = `已${mode === 'cut' ? '剪切' : '复制'} ${selection.value.length} 项，在此视频库的目标目录粘贴`
  }
  async function copyPath() {
    try {
      const paths = selection.value.length ? selection.value : [path.value || '/']
      await navigator.clipboard.writeText(paths.join('\n'))
      message.value = '已复制库内路径'
    } catch (e) { message.value = '无法复制路径：' + e.message }
  }
  function beginRename() {
    if (!canRename.value) return
    inputName.value = one.value.name
    prompt.value = { type: 'rename', library_id: libraryId.value, from: one.value.rel, original: one.value.name, path: path.value, plans: null }
  }
  function beginMkdir() {
    if (!writable.value || busy.value || loading.value || loadError.value) return
    inputName.value = ''
    prompt.value = { type: 'mkdir', library_id: libraryId.value, path: path.value }
  }
  watch(inputName, () => {
    if (prompt.value?.type === 'rename') prompt.value = { ...prompt.value, plans: null }
  }, { flush: 'sync' })
  const post = (url, body) => api('/api/fs/' + url, { method: 'POST', body: JSON.stringify(body) })
  async function runBusy(action, label) {
    if (busy.value) return
    busy.value = true
    message.value = ''
    try { await action() }
    catch (e) { if (!disposed) message.value = label + '：' + e.message }
    finally { if (!disposed) busy.value = false }
  }
  async function refreshAfter(id) {
    if (!disposed && id === libraryId.value) {
      // A background completion must not cancel the directory the user is opening.
      if (loading.value || loadError.value) { refreshQueued = id; return }
      treeCache.value = {}
      await load(path.value, { record: false, clearMessage: false })
      if (path.value) {
        try {
          const data = await api(fsListUrl(id, '', 0, 1))
          if (!disposed && id === libraryId.value) treeCache.value = { ...treeCache.value, '': data.dirs || [] }
        } catch (e) { message.value += '；目录树刷新失败：' + e.message }
      }
    }
  }
  async function submitName() {
    const p = prompt.value
    const name = inputName.value.trim()
    if (!p || !name || /[\\/]/.test(name) || name === '.' || name === '..') { message.value = '请输入合法的单个文件或目录名称'; return }
    if (p.type === 'rename' && name === p.original) { message.value = '名称没有变化'; return }
    await runBusy(async () => {
      if (p.type === 'mkdir') {
        await post('mkdir', { library_id: p.library_id, path: p.path, name })
        prompt.value = null
        message.value = `已创建「${name}」`
        changed(p.library_id, 'mkdir')
        await refreshAfter(p.library_id)
      } else if (!p.plans) {
        const data = await post('rename', { library_id: p.library_id, from: p.from, name, dry_run: true })
        if (prompt.value === p && inputName.value.trim() === name) prompt.value = { ...p, name, plans: data.plans || [] }
      } else {
        if (!p.plans.length || p.plans.some(plan => plan.status !== 'planned')) return
        const data = await post('rename', { library_id: p.library_id, from: p.from, name: p.name, dry_run: false })
        const result = data.results?.[0]
        prompt.value = null
        message.value = result?.status === 'moved' ? `已改名${result.followed ? `，${result.followed} 个关联文件跟随` : ''}` : '改名未完成：' + (result?.status || '未知结果')
        changed(p.library_id, 'rename', (data.results || []).filter(item => item.status === 'moved').length)
        await refreshAfter(p.library_id)
      }
    }, '操作失败')
  }
  async function beginDelete() {
    if (!writable.value || !selection.value.length || loading.value || loadError.value) return
    if (selection.value.length > 100) { message.value = '每次最多删除 100 项，请缩小选择范围'; return }
    const snapshot = { library_id: libraryId.value, paths: [...selection.value] }
    await runBusy(async () => {
      const data = await post('delete', { ...snapshot, dry_run: true })
      prompt.value = { type: 'delete', ...snapshot, plans: data.plans || [] }
    }, '删除预览失败')
  }
  async function confirmDelete() {
    const p = prompt.value
    if (p?.type !== 'delete') return
    const paths = p.plans.filter(plan => !plan.status || plan.status === 'planned_rmdir').map(plan => plan.rel)
    if (!paths.length) return
    await runBusy(async () => {
      const data = await post('delete', { library_id: p.library_id, paths, dry_run: false, confirm: true })
      prompt.value = null
      message.value = `已物理删除 ${data.deleted || 0} 项（不可恢复）` + (data.deleted < paths.length ? '，部分项目未删除，请刷新核对' : '')
      changed(p.library_id, 'delete', data.deleted || 0)
      await refreshAfter(p.library_id)
    }, '删除失败')
  }
  async function paste() {
    if (!canPaste.value) return
    await runBusy(async () => {
      const snapshot = fsPasteSnapshot(clipboard.value, libraryId.value, path.value)
      if (snapshot.mode === 'cut') {
        const plans = []
        for (const from of snapshot.from) {
          try {
            const preview = await post('move', { library_id: snapshot.library_id, from, to_dir: snapshot.to_dir, dry_run: true })
            plans.push(...(preview.plans || []))
          } catch (e) { plans.push({ from, status: e.message }) }
        }
        prompt.value = { type: 'move', ...snapshot, plans }
      } else {
        const preview = await post('copy', { ...snapshot, dry_run: true })
        if (preview.needs_confirm || preview.conflicts?.length) {
          prompt.value = { type: 'copy', ...snapshot, preview }
        } else await startCopy(snapshot)
      }
    }, '粘贴预览失败')
  }
  async function startCopy(snapshot) {
    const data = await post('copy', { library_id: snapshot.library_id, from: snapshot.from, to_dir: snapshot.to_dir, dry_run: false })
    jobs.value = [...jobs.value, { job_id: data.job_id, library_id: snapshot.library_id, to_dir: snapshot.to_dir, state: 'running', total: data.total, bytes_total: data.bytes, done: 0, bytes_done: 0 }]
    message.value = '复制已开始，可继续浏览目录'
    changed(snapshot.library_id, 'copy_started', 0)
    copyPoll.start()
  }
  async function confirmPaste() {
    const p = prompt.value
    if (!p || !['move', 'copy'].includes(p.type)) return
    await runBusy(async () => {
      prompt.value = null
      if (p.type === 'copy') { await startCopy(p); return }
      let moved = 0
      const failures = []
      try {
        for (const plan of p.plans.filter(item => item.status === 'planned')) {
          const data = await post('move', { library_id: p.library_id, from: plan.from, to_dir: p.to_dir, dry_run: false })
          if (data.results?.[0]?.status === 'moved') moved++
          else failures.push(plan.from)
        }
        clipboard.value = { mode: 'copy', library_id: null, rels: [] }
        message.value = `已移动 ${moved} 项` + (failures.length ? `，${failures.length} 项失败，请刷新核对` : '')
      } finally {
        changed(p.library_id, 'move', moved)
        await refreshAfter(p.library_id)
      }
    }, '粘贴失败')
  }
  async function pollCopy() {
    for (const job of [...jobs.value].filter(j => fsJobRunning(j))) {
      try {
        const data = await api('/api/fs/copy/' + job.job_id)
        if (disposed) return
        jobs.value = jobs.value.map(j => j.job_id === job.job_id ? { ...j, ...data } : j)
        if (!fsJobRunning(data)) {
          changed(job.library_id, 'copy', (data.results || []).filter(item => !String(item.status).startsWith('error:')).length)
          await refreshAfter(job.library_id)
        }
      } catch (e) {
        if (!disposed) message.value = '复制进度暂不可用，将重试：' + e.message
      }
    }
    if (!hasAnyRunningTask.value) copyPoll.stop()
  }
  const copyPoll = usePolling(pollCopy, { interval: 1000 })
  async function cancelCopy(job) {
    try { await api('/api/fs/copy/' + job.job_id + '/cancel', { method: 'POST' }) }
    catch (e) { message.value = '取消复制失败：' + e.message }
  }
  function jobProgress(job) {
    if (job.bytes_total) return Math.min(100, Math.round((job.bytes_done || 0) * 100 / job.bytes_total))
    return job.total ? Math.min(100, Math.round((job.done || 0) * 100 / job.total)) : 0
  }
  function jobText(job) {
    const state = job.state === 'cancelled' && job.worker_finished === false ? '正在取消' : { running: '复制中', pending: '等待复制', done: '复制完成', cancelled: '复制已取消', failed: '复制失败', error: '复制失败' }[job.state] || job.state
    const issues = fsCopyIssues(job)
    const issueText = issues.length ? `（${issues.length} 项需核对）` : ''
    return `${state}${issueText} ${job.done || 0}/${job.total || 0} 项 · ${fmtBytes(job.bytes_done || 0)} / ${fmtBytes(job.bytes_total || 0)}` + (job.error ? ' · ' + job.error : '')
  }
  watch(() => props.pendingChanges?.active_jobs, activeJobs => {
    for (const j of activeJobs || []) {
      const id = typeof j === 'string' ? j : j.job_id || j.id
      if (id && !jobs.value.some(job => job.job_id === id)) jobs.value.push({ ...(typeof j === 'object' ? j : {}), job_id: id, library_id: Number(j.library_id || props.pendingChanges.library_id), state: j.state || 'running' })
    }
    if (hasAnyRunningTask.value && !copyPoll.active.value) copyPoll.start()
  }, { immediate: true, deep: true })
  watch(libraryId, () => {
    loader.cancel()
    loadGeneration++
    historyGeneration++
    refreshQueued = null
    loading.value = false
    hasLoaded.value = false
    loadError.value = ''
    loadTarget.value = ''
    loadedCount.value = 0
    totalCount.value = 0
    path.value = ''
    dirs.value = []
    files.value = []
    crumbs.value = []
    prompt.value = null
    closePreview()
    query.value = ''
    writable.value = false
    history.value = ['']
    historyIndex.value = 0
    treeCache.value = {}
    treeExpanded.value = new Set([''])
    treeLoading.value = new Set()
    if (props.active) load('')
  }, { immediate: true })
  watch(() => props.active, active => {
    if (active) load(path.value, { record: false })
    else closePreview()
  }, { flush: 'sync' })
  watch(rows, current => { const visible = new Set(current.map(r => r.rel)); selection.value = selection.value.filter(rel => visible.has(rel)) })
  onUnmounted(() => { closePreview(); disposed = true; loadGeneration++; loader.cancel(); copyPoll.stop() })
  return { libraryId, library, path, dirs, files, crumbs, writable, loading, hasLoaded, loadError, loadTarget, loadState, loadedCount, totalCount, message, busy, query, sort, direction,
    selection, focused, clipboard, prompt, previewFile, inputName, jobs, history, historyIndex, treeExpanded, treeLoading, rows, one, selectedRows,
    canRename, canCut, canPaste, pending, hasRunningTask, hasPendingOperation, treeRows,
    scan, switchLibrary, navigate, load, retryLoad, historyMove, up, toggleTree, select, selectAll, moveFocus, open, viewDetails, closePreview, setSort, copy, copyPath,
    beginRename, beginMkdir, submitName, beginDelete, confirmDelete, paste, confirmPaste, cancelCopy, jobProgress, jobText, jobRunning: fsJobRunning, jobIssues: fsCopyIssues, resetSelection }
}
