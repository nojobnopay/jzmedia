// ClientCapabilities 检测（目标文档 §4）：进入播放/详情时实测浏览器真实解码能力。
// 两层：
// 1) 通用矩阵（h264/hevc/hevc10/av1… × aac/ac3/eac3/dts…）→ 缓存 24h；
// 2) 逐片候选码串（服务端 media_info.vcaps + 每音轨 caps）→ decide 前实测回传，
//    避免“支持 H264 ≠ 支持 Hi10P / Main10 超分辨率档”这类粗判。
// 文件直发与 MSE 分别检测；原生 HLS 使用文件解码能力，不依赖 MSE。
const STORAGE_KEY = 'jzmedia.caps.v4'
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

function mediaSource () {
  return window.MediaSource || window.ManagedMediaSource || window.WebKitMediaSource
}

function msSupport (type) {
  try {
    return !!mediaSource()?.isTypeSupported(type)
  } catch (e) { return false }
}

function fileSupport (type) {
  try { return !!document.createElement('video').canPlayType(type) }
  catch (e) { return false }
}

// 与 hls.js 的基础支持检查一致：接口存在不代表可用，至少要能接收一种 MP4 编码。
export function playbackSupport () {
  let mse = false, native = false, managed = false, audioTracks = false
  try {
    const sb = window.SourceBuffer || window.WebKitSourceBuffer
    const validBuffer = !sb || (typeof sb.prototype?.appendBuffer === 'function' && typeof sb.prototype?.remove === 'function')
    mse = validBuffer && ['video/mp4; codecs="avc1.42E01E,mp4a.40.2"',
      'video/mp4; codecs="av01.0.01M.08"', 'video/mp4; codecs="vp09.00.50.08"',
      'audio/mp4; codecs="mp4a.40.2"', 'audio/mp4; codecs="fLaC"'].some(msSupport)
    const v = document.createElement('video')
    native = !!(v.canPlayType('application/vnd.apple.mpegurl') || v.canPlayType('application/x-mpegURL'))
    managed = !!window.ManagedMediaSource
    audioTracks = 'audioTracks' in v
  } catch (e) { /* 保留已检测结果 */ }
  const mp4 = !mse && /MiuiBrowser/i.test(navigator.userAgent || '')
  return { mse, native_hls_available: native, native_hls: !mp4 && native && (managed || !mse), mp4_compat: mp4,
    hls_compat: native && !managed && !mse, native_audio_tracks: audioTracks }
}

async function decInfo (type, kind, extra, mode) {
  try {
    const mc = navigator.mediaCapabilities
    if (!mc || !mc.decodingInfo) return null
    const cfg = { type: mode, [kind]: { contentType: type, ...(extra || {}) } }
    const r = await mc.decodingInfo(cfg)
    return !!(r && r.supported)
  } catch (e) { return null }
}

// 候选码串实测（逐片用）：video/audio 自动分流；返回 {string: bool}
const probeCache = new Map()
export async function probeStrings (strings, dims, mode = 'media-source') {
  const out = {}
  const list = [...new Set(strings || [])]
  await Promise.all(list.map(async (s) => {
    const w = Number(dims && dims.w) || 1920
    const h = Number(dims && dims.h) || 1080
    const key = `${mode}|${s}|${w}x${h}`
    if (probeCache.has(key)) { out[s] = probeCache.get(key); return }
    let ok = null
    if (s.startsWith('video/')) {
      ok = await decInfo(s, 'video', { width: w, height: h, bitrate: 8e6, framerate: 24 }, mode)
    } else if (s.startsWith('audio/')) {
      ok = await decInfo(s, 'audio', { channels: 2, samplerate: 48000, bitrate: 192000 }, mode)
    }
    if (ok === null) ok = mode === 'file' ? fileSupport(s) : msSupport(s)
    probeCache.set(key, ok)
    out[s] = ok
  }))
  return out
}

export function withProbes (caps, probes, mode = 'media-source') {
  if (mode === 'file') return { ...caps, file: { ...caps?.file, probes: { ...caps?.file?.probes, ...probes } } }
  return { ...(caps || {}), probes: { ...((caps || {}).probes || {}), ...(probes || {}) } }
}

async function anySupport (codecs, w, h, kind, transferFunction, mode = 'media-source') {
  const mt = kind === 'audio' ? 'audio/mp4' : 'video/mp4'
  for (const c of codecs) {
    const type = `${mt}; codecs="${c}"`
    let ok = null
    if (kind === 'audio') ok = await decInfo(type, 'audio', { channels: 2, samplerate: 48000, bitrate: 192000 }, mode)
    else {
      const extra = { width: w || 1920, height: h || 1080, bitrate: 8e6, framerate: 24 }
      if (transferFunction) extra.transferFunction = transferFunction
      ok = await decInfo(type, 'video', extra, mode)
    }
    if (ok === null) ok = transferFunction ? false : (mode === 'file' ? fileSupport(type) : msSupport(type))
    if (ok) return true
  }
  return false
}

async function detectFormats (mode) {
  const video = {}
  for (const [key, w, h, codecs] of VIDEO_TESTS) video[key] = await anySupport(codecs, w, h, 'video', undefined, mode)
  const audio = {}
  for (const [key, codecs] of AUDIO_TESTS) audio[key] = await anySupport(codecs, 0, 0, 'audio', undefined, mode)
  // 解码端 HDR 能力（与显示器是否 HDR 无关）：浏览器能解 PQ → 服务端放行 HDR/DV 原画
  // 直通，显示映射（HDR 直通或 tone map 到 SDR）交给浏览器/系统（Plex 式）。
  // L153/L150 都探：部分硬件只到 4K30；Chrome/Safari 的 PQ 声明才准。
  const hdrDecode = await anySupport(
    ['hvc1.2.4.L153.B0', 'hev1.2.4.L153.B0',
     'hvc1.2.4.L150.B0', 'hev1.2.4.L150.B0', 'av01.0.12M.10'],
    3840, 2160, 'video', 'pq', mode)
  let hdr = false
  try {
    hdr = !!(window.matchMedia && window.matchMedia('(dynamic-range: high)').matches)
  } catch (e) { hdr = false }
  // 旧字段语义保留：显示器 HDR 且能解 PQ（服务端兼容期仍认它）
  if (hdr) hdr = hdrDecode
  return { video, audio, hdr, hdr_decode: hdrDecode, probes: {} }
}

async function detect () {
  const support = playbackSupport()
  const [mse, file] = await Promise.all([detectFormats('media-source'), detectFormats('file')])
  return { ...(support.native_hls || support.mp4_compat ? file : mse), ...support, file }
}

let basePromise = null

// 基础能力（通用矩阵）：内存 + sessionStorage 双层缓存（UA/动态范围变化即失效）
export function getCaps () {
  if (basePromise) return basePromise
  basePromise = (async () => {
    let envKey = ''
    try {
      const dr = window.matchMedia && window.matchMedia('(dynamic-range: high)').matches
      const support = playbackSupport()
      envKey = `${navigator.userAgent}|${dr ? 'hdr' : 'sdr'}|mse=${support.mse}|native=${support.native_hls_available}`
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

// 调试口：DevTools 里 `await __jzCaps()`；sessionStorage 的 v4 缓存刷新后重测。
try { window.__jzCaps = getCaps } catch (e) { /* 忽略 */ }
