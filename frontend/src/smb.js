// SMB 地址解析（与后端 app/smburl.py 同一组测试向量）：建库/编辑连接单输入框实时预览用。
// 输入：\\主机\共享\目录、//主机/共享/目录、smb://[用户@]主机/共享/目录
const SCHEME_RE = /^smb:\/\//i
const DRIVE_RE = /^[A-Za-z]:[\\/]/

function unquote (s) {
  try { return decodeURIComponent(s) } catch (e) { return s }
}

export function parseSmbInput (raw) {
  const text = String(raw || '').trim()
  if (!text) {
    return { host: '', share: '', subpath: '', username: '',
      error: '请输入服务器/共享路径，如 \\\\ServerName\\ShareName' }
  }
  if (DRIVE_RE.test(text)) {
    return { host: '', share: '', subpath: '', username: '',
      error: '这看起来是 Windows 映射盘路径；请填 NAS 的 \\\\主机\\共享\\目录，或容器的本地路径' }
  }
  const isUrl = SCHEME_RE.test(text)
  let body = text.replace(SCHEME_RE, '')
  let username = ''
  const head = body.split('/', 1)[0]
  if (isUrl && head.includes('@')) {
    const idx = body.indexOf('@')
    const userinfo = body.slice(0, idx)
    body = body.slice(idx + 1)
    username = unquote(userinfo.split(';').pop() || '')
  }
  body = body.replace(/\\/g, '/')
  const segs = body.split('/').filter(Boolean)
  if (segs.length < 2) {
    return { host: segs[0] || '', share: '', subpath: '', username,
      error: '还需要共享名，形如 \\\\ServerName\\ShareName' }
  }
  const host = isUrl ? unquote(segs[0]) : segs[0]
  const share = isUrl ? unquote(segs[1]) : segs[1]
  if (!host) {
    return { host: '', share: '', subpath: '', username, error: '服务器地址不能为空' }
  }
  if (!share) {
    return { host, share: '', subpath: '', username, error: '共享名不能为空' }
  }
  const parts = isUrl ? segs.slice(2).map(unquote) : segs.slice(2)
  return { host, share, subpath: parts.join('/'), username, error: '' }
}

// 库记录 → 可编辑的完整地址（编辑连接回填）
export function smbUrlOf (lib) {
  const host = (lib && lib.smb_host) || ''
  const share = (lib && lib.smb_share) || ''
  const sub = (lib && lib.smb_subpath) || ''
  if (!host) return ''
  return '\\\\' + host + '\\' + share + (sub ? '\\' + sub.split('/').join('\\') : '')
}
