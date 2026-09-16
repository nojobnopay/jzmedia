// 通用轮询 composable（评审 B9/R06-Q1 + R07-Q1）：Collections 补全进度、Detail 预转码
// 等此前各写一遍 setInterval/清理逻辑，这里统一并自动在卸载时停止。
import { onUnmounted, ref } from 'vue'

export function usePolling(tick, { interval = 3000, immediate = false } = {}) {
  const active = ref(false)
  let timer = null
  let running = false

  async function run() {
    if (!active.value || running) return
    running = true
    try {
      await tick()
    } catch (e) {
      /* 单次失败不打断轮询，下次继续 */
    } finally {
      running = false
    }
  }

  function start() {
    stop()
    active.value = true
    if (immediate) run()
    timer = setInterval(run, interval)
  }

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    active.value = false
  }

  onUnmounted(stop)
  return { active, start, stop }
}
