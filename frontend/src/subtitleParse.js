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
