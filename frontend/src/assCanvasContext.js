// ASS 是透明覆盖层，必须和视频一起合成。JASSUB 的 desynchronized 提示可能让
// Chrome 将画布直接交给显示控制器；这里只在专用 worker 中关闭这条低延迟路径。
// 保留 WebGL、alpha 和其他上下文选项，不影响页面或其他播放器画布。
export function useCompositedAssContexts(Canvas) {
  const prototype = Canvas?.prototype
  if (typeof prototype?.getContext !== 'function') return
  const getContext = prototype.getContext
  prototype.getContext = function (type, attributes) {
    if (type === 'webgl' || type === 'webgl2' || type === '2d') {
      return getContext.call(this, type, { ...attributes, desynchronized: false })
    }
    return Reflect.apply(getContext, this, arguments)
  }
}

useCompositedAssContexts(globalThis.OffscreenCanvas)
