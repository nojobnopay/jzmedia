// 播放掉帧看门狗（纯函数，node --test 单测）：
// 原画直通（视频 copy：direct/remux/audio_transcode）且源 >1080p 时，若浏览器硬解
// 跟不上会持续丢帧（如 4K HEVC 在部分核显/驱动上）。这里只负责"判定该提示了"，
// 动作由播放器决定（提示 + 用户手点降档；不自动切、不落 localStorage）。
export const DROP_WINDOW_MS = 30000
export const DROP_TRIGGER_COUNT = 5
export const DROP_MIN_UPTIME_MS = 15000

export function createDropGuard () {
  return { started: false, at: 0, dropped: 0, total: 0 }
}

// 只有"视频未转码"的直通路径才可能因客户端解码能力丢帧；已转码路径不参与。
export function isCopyVideoPath (method, srcHeight) {
  const m = String(method || '')
  const copy = m === 'direct' || m === 'remux' || m === 'audio_transcode'
  return copy && Number(srcHeight) > 1080
}

// 每拍采样一次。返回 { fire, drops, frames }：
// fire=刚结束的窗口判定应提示；drops/frames=该窗口内增量（未满窗时都为 0）。
// 暂停/seek/计数器异常时重置窗口，避免把历史累计或异常跳变算进来。
export function tickDropGuard (g, sample, ctx) {
  const out = { fire: false, drops: 0, frames: 0 }
  const now = Number(ctx && ctx.now) || 0
  const playing = !!(ctx && ctx.playing)
  const uptime = now - (Number(ctx && ctx.startedAt) || 0)
  const dropped = Number(sample && sample.dropped)
  const total = Number(sample && sample.total)
  if (!playing || !Number.isFinite(dropped) || !Number.isFinite(total)
      || dropped < 0 || total < dropped) {
    g.started = false
    return out
  }
  if (!g.started) {
    g.started = true
    g.at = now
    g.dropped = dropped
    g.total = total
    return out
  }
  if (now - g.at < DROP_WINDOW_MS) return out
  out.drops = dropped - g.dropped
  out.frames = total - g.total
  g.at = now
  g.dropped = dropped
  g.total = total
  out.fire = uptime >= DROP_MIN_UPTIME_MS && out.frames > 0 &&
    out.drops >= DROP_TRIGGER_COUNT
  return out
}
