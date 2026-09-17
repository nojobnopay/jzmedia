// 字幕解析纯逻辑（评审 R13-Q1）：从 PlayerModal 抽出，可 node 单测。
// 会话偏移/用户延迟不在解析期叠加（渲染时叠加，便于即时调整）。

export function vttMs(s) {
  const m = /^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})$/.exec(String(s || '').trim())
  if (!m) return null
  return ((Number(m[1] || 0) * 60 + Number(m[2])) * 60 + Number(m[3])) * 1000 + Number(m[4])
}

// 文本去标签 + 实体解码（textContent 渲染，纯文本安全）；无 DOM（node 测试）时仅去标签。
export function vttPlain(raw) {
  const s = String(raw || '').replace(/<[^>]*>/g, '')
  try {
    const doc = globalThis.document
    if (!doc) return s.trim()
    const el = doc.createElement('textarea')
    el.innerHTML = s
    return el.value.trim()
  } catch (e) { return s.trim() }
}

// 解析 WebVTT → cue 列表（源时间轴毫秒）；STYLE/REGION/注释块跳过。
// SRT 可直接复用本函数（时间戳用逗号，vttMs 兼容 [.,]，序号行被块内 --> 搜索忽略）。
export function parseVtt(text) {
  const cues = []
  for (const block of String(text || '').split(/\r?\n\r?\n/)) {
    const lines = block.split(/\r?\n/)
    let ti = -1
    for (let i = 0; i < lines.length; i++) {
      if (/^\s*\S+\s*-->\s*\S+/.test(lines[i])) { ti = i; break }
    }
    if (ti < 0) continue
    const m = /^(\s*)(\S+)\s*-->\s*(\S+)\s*(.*)$/.exec(lines[ti])
    if (!m) continue
    const a = vttMs(m[2])
    const b = vttMs(m[3])
    if (a == null || b == null || b <= a) continue
    const am = /(?:^|\s)align:(\w+)/.exec(m[4] || '')
    cues.push({ start: a, end: b, text: vttPlain(lines.slice(ti + 1).join('\n')),
                align: am ? am[1] : '' })
  }
  cues.sort((x, y) => x.start - y.start || x.end - y.end)
  return cues
}

// 自动选轨（评审 B8/R13-D2 + 用户反馈）：中文优先（中文 default → 首条中文 → 任意 default），
// 即外语 default 不压中文字幕；PGS 客户端渲染（pgs_client）可自动选，零转码；
// VobSub 等烧录轨不自动选（防意外触发重编）。
export function pickDefaultSub(list) {
  const arr = Array.isArray(list) ? list : []
  const autoOk = (s) => !!s && (!s.image || String(s.codec || '').toLowerCase() === 'pgs')
  const zh = (s) => String((s || {}).lang || '').toLowerCase().startsWith('chi')
  let i = arr.findIndex(s => autoOk(s) && zh(s) && Number(s.default) === 1)
  if (i < 0) i = arr.findIndex(s => autoOk(s) && zh(s))
  if (i < 0) i = arr.findIndex(s => autoOk(s) && Number(s.default) === 1)
  return i
}

// 当前时刻命中的 cue（源时间轴毫秒；cues 已按 start 升序）。
// 从 vttRender 抽出（评审 R13-Q4），命中判定可单测，避免字幕错位类回归。
export function activeCues(cues, timeMs) {
  const t = Number(timeMs) || 0
  const out = []
  for (const c of cues || []) {
    if (t >= c.start && t < c.end) out.push(c)
    else if (c.start > t) break
  }
  return out
}

// 本地字幕文件解码（2026-09 播放器临时加载）：BOM 优先，UTF-8 严格解失败回落
// GB18030（简中 SRT 常见编码）；极端异常返回 ''。纯函数，可 node 单测。
export function decodeSubtitleBytes(buf) {
  let b = buf instanceof Uint8Array ? buf : new Uint8Array(buf || [])
  try {
    if (b.length >= 2 && b[0] === 0xFF && b[1] === 0xFE) {
      return new TextDecoder('utf-16le').decode(b.subarray(2))
    }
    if (b.length >= 2 && b[0] === 0xFE && b[1] === 0xFF) {
      return new TextDecoder('utf-16be').decode(b.subarray(2))
    }
    if (b.length >= 3 && b[0] === 0xEF && b[1] === 0xBB && b[2] === 0xBF) {
      b = b.subarray(3)
    }
    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(b)
    } catch (e) {
      return new TextDecoder('gb18030').decode(b)
    }
  } catch (e) {
    try { return new TextDecoder('utf-8').decode(b) } catch (e2) { return '' }
  }
}

// 本地字幕文件 → codec（仅文本格式；图片字幕 .sup/.sub/.idx 不支持临时加载，返回 ''）。
// srt/vtt 走自绘层（parseVtt），ass/ssa 交给 JASSUB（blob URL）。
export function localSubCodec(filename) {
  const ext = String(filename || '').split('.').pop().toLowerCase()
  if (ext === 'srt' || ext === 'vtt' || ext === 'ass' || ext === 'ssa') return ext
  return ''
}
