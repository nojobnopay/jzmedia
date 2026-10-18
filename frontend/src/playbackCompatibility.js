// 兼容重播只影响当前播放器，不写入浏览器基础能力缓存。
export function compatibilityCaps(caps, { native = false, transcode = false } = {}) {
  const decode = native ? (caps.file || caps) : caps
  return { ...caps, video: transcode ? {} : { ...decode.video },
    audio: { ...decode.audio },
    probes: Object.fromEntries(Object.entries(decode.probes || {}).map(([k, v]) => [k, transcode ? false : v])),
    hdr: false, hdr_decode: false, native_hls: native,
    hls_compat: true, force_hls: true }
}

export function blockedPlaybackMessage(reasons = []) {
  if (reasons.includes('dovi_p5_source_unsupported')) return '此杜比视界片源暂不支持网页颜色转换，请复制直链交给外部播放器，或选择兼容版本'
  if (reasons.includes('dovi_p5_burn_unsupported')) return '此杜比视界版本暂不支持图片字幕烧录，请关闭该字幕后重试，或复制直链交给外部播放器'
  if (reasons.includes('dovi_p5_packaging_unsupported')) return '此浏览器无法接收杜比视界转换后的播放格式，请复制直链交给外部播放器，或选择兼容版本'
  if (reasons.includes('dovi_p5_web_disabled')) return '服务器未启用杜比视界网页转换，请使用电视原视频播放、复制直链给外部播放器，或选择兼容版本'
  if (reasons.includes('dovi_p5_unavailable')) return '服务器暂时无法正确、流畅地转换此杜比视界版本，请复制直链交给外部播放器，或选择兼容版本'
  if (reasons.includes('no_mse')) return '此浏览器无法接收这部影片需要的播放格式，可用 Chrome/Edge 重试，或在播放设置中复制直链给外部播放器'
  return '当前浏览器无法播放此版本，可尝试其他版本，或在播放设置中复制直链给外部播放器'
}

export function mediaErrorMessage(code) {
  if (code === 2) return '视频加载失败，请检查网络后重试'
  if (code === 3 || code === 4) return '浏览器无法解析或解码此视频，可切换 720p 重试，或在播放设置中复制直链给外部播放器'
  return '播放中断，请重试或在播放设置中复制诊断信息'
}
