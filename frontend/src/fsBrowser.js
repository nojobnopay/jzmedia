// File browser rules shared by mouse, keyboard, and operation previews.
export function fsType(row) {
  return row.isDir ? '文件夹' : ({ feature: '正片', sidecar: '花絮', subtitle: '字幕', nfo: 'NFO', other: '其他' }[row.kind] || '其他')
}

export function fsMatchStatus(row) {
  if (row.isDir || row.kind !== 'feature') return '—'
  if (row.match_status === 'matched') return '已匹配'
  if (row.match_status === 'unmatched') return '待匹配'
  if (row.match_status === 'unregistered') return '待扫描'
  return row.movie_id || row.episode_id ? (row.tmdb_id ? '已匹配' : '待匹配') : '待扫描'
}

export function fsVisibleRows(dirs, files, query = '', sort = 'name', direction = 1) {
  const needle = query.trim().toLocaleLowerCase()
  return [...dirs.map(d => ({ ...d, isDir: true })), ...files.map(f => ({ ...f, isDir: false }))]
    .filter(row => !needle || row.name.toLocaleLowerCase().includes(needle))
    .sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
      let compared = 0
      if (sort === 'name') compared = a.name.localeCompare(b.name, 'zh-CN', { numeric: true })
      else if (sort === 'type') compared = fsType(a).localeCompare(fsType(b), 'zh-CN')
      else if (sort === 'status') compared = fsMatchStatus(a).localeCompare(fsMatchStatus(b), 'zh-CN')
      else compared = (Number(a[sort]) || 0) - (Number(b[sort]) || 0)
      return direction * (compared || a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
    })
}

export function fsSelect(rows, selected, anchor, rel, { shiftKey = false, ctrlKey = false, metaKey = false } = {}) {
  const additive = ctrlKey || metaKey
  if (shiftKey && rows.some(r => r.rel === anchor)) {
    const start = rows.findIndex(r => r.rel === anchor)
    const end = rows.findIndex(r => r.rel === rel)
    if (end < 0) return { selected, anchor }
    const range = rows.slice(Math.min(start, end), Math.max(start, end) + 1).map(r => r.rel)
    return { selected: additive ? [...new Set([...selected, ...range])] : range, anchor }
  }
  return {
    selected: additive ? (selected.includes(rel) ? selected.filter(r => r !== rel) : [...selected, rel]) : [rel],
    anchor: rel,
  }
}

export function fsPasteSnapshot(clipboard, libraryId, path) {
  if (!clipboard.rels.length) throw new Error('请先选择要复制或剪切的文件')
  if (Number(clipboard.library_id) !== Number(libraryId)) throw new Error('不支持跨视频库粘贴，请在原视频库内选择目标目录')
  return Object.freeze({ library_id: Number(libraryId), to_dir: path, from: Object.freeze([...clipboard.rels]), mode: clipboard.mode })
}

export function fsListUrl(libraryId, path = '', offset = 0, limit = 1000) {
  return '/api/fs/list?' + new URLSearchParams({ library: libraryId, path, offset, limit })
}

// Load each page with the same captured library/path. A cancelled generation never
// publishes any of its pages, even when a mock or upstream ignores abort signals.
export function createFsDirectoryLoader(request) {
  let generation = 0
  let controller = null
  function cancel() {
    generation++
    controller?.abort()
  }
  async function load(libraryId, path, publish, progress = () => {}) {
    cancel()
    const mine = generation
    controller = new AbortController()
    const signal = controller.signal
    let offset = 0
    let first = null
    const files = []
    while (true) {
      const page = await request(fsListUrl(libraryId, path, offset), { signal })
      if (mine !== generation) return false
      if (!first) first = page
      files.push(...(page.files || []))
      progress(files.length, page.total_files ?? files.length)
      if (!page.has_more) break
      const next = Number(page.offset ?? offset) + (page.files || []).length
      if (next <= offset) throw new Error('目录分页未前进，请刷新重试')
      offset = next
    }
    if (mine !== generation) return false
    publish({ ...first, files: [...new Map(files.map(f => [f.rel, f])).values()] })
    return true
  }
  return { load, cancel }
}

export function fsJobRunning(job) {
  return ['running', 'pending'].includes(job.state) || (job.state === 'cancelled' && job.worker_finished === false)
}

export function fsCopyIssues(job) {
  return (job.results || []).filter(item => /^(error:|copied_scan_warn:)/.test(String(item.status)))
}
