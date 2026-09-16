// ClientCapabilities 检测（目标文档 §4）：进入播放/详情时实测浏览器真实解码能力。
// 两层：
// 1) 通用矩阵（h264/hevc/hevc10/av1… × aac/ac3/eac3/dts…）→ 缓存 24h；
// 2) 逐片候选码串（服务端 media_info.vcaps + 每音轨 caps）→ decide 前实测回传，
//    避免“支持 H264 ≠ 支持 Hi10P / Main10 超分辨率档”这类粗判。
// 实测优先 navigator.mediaCapabilities.decodingInfo，回落 MediaSource.isTypeSupported。
const STORAGE_KEY = 'jzmedia.caps.v1'
const TTL = 24 * 3600 * 1000

const VIDEO_TESTS = [
  ['h264', 1920, 1080, ['avc1.42E01E', 'avc1.4D401E', 'avc1.640028', 'avc1.640033']],
  ['h264_hi10p', 1920, 1080, ['avc1.6E0028']],
  ['hevc', 3840, 2160, ['hvc1.1.6.L120.B0', 'hev1.1.6.L120.B0', 'hvc1.1.6.L93.B0']],
  ['hevc10', 3840, 2160, ['hvc1.2.4.L150.B0', 'hev1.2.4.L150.B0']],
  ['av1', 3840, 2160, ['av01.0.08M.08', 'av01.0.12M.10']],
  ['vp9', 1920, 1080, ['vp09.00.10.08', 'vp09.00.10.10']],
  ['mpeg2', 1920, 1080, ['mp2v']],
  ['vc1', 1920, 1080, ['vc-1']],
]
const AUDIO_TESTS = [
  ['aac', ['mp4a.40.2', 'mp4a.40.5']],
  ['mp3', ['mp3', 'mp4a.6b']],
  ['ac3', ['ac-3']],
  ['eac3', ['ec-3']],
  ['dts', ['dtsc', 'dtsh', 'dtsl', 'dtse']],
  ['truehd', ['mlpa']],
  ['flac', ['flac']],
  ['opus', ['opus']],
  ['vorbis', ['vorbis']],
  ['pcm', ['lpcm', 'pcm-s16']],
]

function msSupport (type) {
  try {
    return !!(window.MediaSource && MediaSource.isTypeSupported(type))
  } catch (e) { return false }
}

async function decInfo (type, kind, extra) {
  try {
    const mc = navigator.mediaCapabilities
    if (!mc || !mc.decodingInfo) return null
    const cfg = { type: 'media-source', [kind]: { contentType: type, ...(extra || {}) } }
    const r = await mc.decodingInfo(cfg)
    return !!(r && r.supported)
  } catch (e) { return null }
}

// 候选码串实测（逐片用）：video/audio 自动分流；返回 {string: bool}
const probeCache = new Map()
export async function probeStrings (strings, dims) {
  const out = {}
  const list = [...new Set(strings || [])]
  await Promise.all(list.map(async (s) => {
    const w = Number(dims && dims.w) || 1920
    const h = Number(dims && dims.h) || 1080
    const key = `${s}|${w}x${h}`
    if (probeCache.has(key)) { out[s] = probeCache.get(key); return }
    let ok = null
    if (s.startsWith('video/')) {
      ok = await decInfo(s, 'video', { width: w, height: h, bitrate: 8e6, framerate: 24 })
    } else if (s.startsWith('audio/')) {
      ok = await decInfo(s, 'audio', { channels: 2, samplerate: 48000, bitrate: 192000 })
    }
    if (ok === null) ok = msSupport(s)
    probeCache.set(key, ok)
    out[s] = ok
  }))
  return out
}

export function withProbes (caps, probes) {
  return { ...(caps || {}), probes: { ...((caps || {}).probes || {}), ...(probes || {}) } }
}

async function anySupport (codecs, w, h, kind, transferFunction) {
  const mt = kind === 'audio' ? 'audio/mp4' : 'video/mp4'
  for (const c of codecs) {
    const type = `${mt}; codecs="${c}"`
    let ok = null
    if (kind === 'audio') ok = await decInfo(type, 'audio', { channels: 2, samplerate: 48000, bitrate: 192000 })
    else {
      const extra = { width: w || 1920, height: h || 1080, bitrate: 8e6, framerate: 24 }
      if (transferFunction) extra.transferFunction = transferFunction
      ok = await decInfo(type, 'video', extra)
    }
    if (ok === null) ok = msSupport(type)
    if (ok) return true
  }
  return false
}

async function detect () {
  const video = {}
  for (const [key, w, h, codecs] of VIDEO_TESTS) video[key] = await anySupport(codecs, w, h)
  const audio = {}
  for (const [key, codecs] of AUDIO_TESTS) audio[key] = await anySupport(codecs, 0, 0, 'audio')
  let hdr = false
  try {
    hdr = !!(window.matchMedia && window.matchMedia('(dynamic-range: high)').matches)
  } catch (e) { hdr = false }
  if (hdr) {
    // HDR 直通还要看 HEVC/AV1 的 PQ 解码声明（Chrome/Safari 才准）
    hdr = await anySupport(['hvc1.2.4.L153.B0', 'hev1.2.4.L153.B0', 'av01.0.12M.10'],
      3840, 2160, 'video', 'pq')
  }
  let nativeHls = false
  try {
    nativeHls = ('ManagedMediaSource' in window) &&
      !!document.createElement('video').canPlayType('application/vnd.apple.mpegurl')
  } catch (e) { nativeHls = false }
  return { video, audio, hdr, mse: !!window.MediaSource, native_hls: nativeHls, probes: {} }
}

let basePromise = null

// 基础能力（通用矩阵）：内存 + sessionStorage 双层缓存（UA/动态范围变化即失效）
export function getCaps () {
  if (basePromise) return basePromise
  basePromise = (async () => {
    let envKey = ''
    try {
      const dr = window.matchMedia && window.matchMedia('(dynamic-range: high)').matches
      envKey = `${navigator.userAgent}|${dr ? 'hdr' : 'sdr'}`
    } catch (e) { envKey = String(navigator.userAgent || '') }
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY)
      if (raw) {
        const c = JSON.parse(raw)
        if (c && c.env === envKey && Date.now() - (c.t || 0) < TTL && c.caps) return c.caps
      }
    } catch (e) { /* 缓存不可用直接实测 */ }
    const caps = await detect()
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ env: envKey, t: Date.now(), caps })) } catch (e) { /* 忽略 */ }
    return caps
  })()
  return basePromise
}

// 调试口：DevTools 里 `await __jzCaps()` 或 `sessionStorage.removeItem('jzmedia.caps.v1')` 后重测
try { window.__jzCaps = getCaps } catch (e) { /* 忽略 */ }
