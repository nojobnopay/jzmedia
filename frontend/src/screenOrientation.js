/**
 * 手机全屏方向：是否尝试锁定横屏的纯判定（浏览器调用见 PlayerModal）。
 *
 * 只在“粗指针 + 竖屏 + 浏览器支持 Screen Orientation API”时锁定：
 * 桌面/已横屏不调用（桌面 Chrome 调 lock 会直接拒绝），iOS 等不支持的
 * 走“旋转设备”弱提示。lock 本身失败一律静默，不打扰播放。
 */
export function landscapeLockWanted(env) {
  const e = env || {}
  return !!(e.coarse && e.portrait && e.canLock)
}

/** 当前环境快照（matchMedia/screen 访问全部 try 保护，SSR/测试环境回落全否）。 */
export function orientationEnv(host) {
  const w = (host && host.window) || (typeof window !== 'undefined' ? window : null)
  const s = (host && host.screen) || (typeof screen !== 'undefined' ? screen : null)
  try {
    const coarse = !!(w && w.matchMedia && w.matchMedia('(pointer: coarse)').matches)
    const portrait = !!(w && w.matchMedia && w.matchMedia('(orientation: portrait)').matches)
    const canLock = !!(s && s.orientation && typeof s.orientation.lock === 'function')
    return { coarse, portrait, canLock }
  } catch (e) {
    return { coarse: false, portrait: false, canLock: false }
  }
}

/** 请求横屏锁定；被拒/不支持静默（调用方已判定，仅做最后一道保护）。 */
export function lockLandscape(host) {
  try {
    const s = (host && host.screen) || (typeof screen !== 'undefined' ? screen : null)
    const p = s && s.orientation && s.orientation.lock && s.orientation.lock('landscape')
    if (p && typeof p.catch === 'function') p.catch(() => {})
  } catch (e) { /* 忽略 */ }
}

/** 退出全屏时解锁；无 API 时为空操作。 */
export function unlockOrientation(host) {
  try {
    const s = (host && host.screen) || (typeof screen !== 'undefined' ? screen : null)
    if (s && s.orientation && typeof s.orientation.unlock === 'function') s.orientation.unlock()
  } catch (e) { /* 忽略 */ }
}
