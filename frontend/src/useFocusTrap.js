// 弹窗焦点陷阱（评审 R14-B6）：Tab/Shift+Tab 在弹窗内循环，打开聚焦、关闭还原。
// 用法：const box = ref(null); useFocusTrap(openRef, box)
import { nextTick, onMounted, onUnmounted, watch } from 'vue'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

// A child dialog owns the keyboard until it closes; the underlying dialog stays mounted.
const traps = []
let scrollBody = null
let previousOverflow = ''

export function useFocusTrap(active, containerRef) {
  let prevActive = null
  const trap = { containerRef, nodes }

  function nodes() {
    const root = containerRef.value
    if (!root) return []
    return [...root.querySelectorAll(FOCUSABLE)]
      .filter(n => !n.matches?.(':disabled') && (n.offsetWidth > 0 || n.offsetHeight > 0))
  }

  function onKeydown(e) {
    if (e.key !== 'Tab' || !active.value || traps.at(-1) !== trap || e.defaultPrevented) return
    const root = containerRef.value
    if (!root) return
    const list = nodes()
    if (!list.length) {
      e.preventDefault()
      return
    }
    const first = list[0]
    const last = list[list.length - 1]
    const cur = document.activeElement
    if (!root.contains(cur)) {
      e.preventDefault()
      ;(e.shiftKey ? last : first).focus()
      return
    }
    if (e.shiftKey && cur === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && cur === last) {
      e.preventDefault()
      first.focus()
    }
  }

  function activate() {
    if (traps.includes(trap)) return
    prevActive = document.activeElement
    if (!traps.length && document.body?.style) {
      scrollBody = document.body
      previousOverflow = scrollBody.style.overflow
      scrollBody.style.overflow = 'hidden'
    }
    traps.push(trap)
    document.addEventListener('keydown', onKeydown, true)
    nextTick(() => {
      if (!active.value || traps.at(-1) !== trap) return
      const list = nodes()
      if (list.length) list[0].focus()
      else if (containerRef.value) {
        containerRef.value.setAttribute('tabindex', '-1')
        containerRef.value.focus()
      }
    })
  }

  function deactivate() {
    document.removeEventListener('keydown', onKeydown, true)
    const index = traps.indexOf(trap)
    if (index === -1) return
    const wasTop = traps.at(-1) === trap
    traps.splice(index, 1)
    if (!traps.length && scrollBody) {
      scrollBody.style.overflow = previousOverflow
      scrollBody = null
    }
    const parent = traps.at(-1)
    if (wasTop) {
      // Removing an underlying dialog must never take focus from its child.
      if (prevActive?.isConnected !== false && prevActive?.focus && (!parent || parent.containerRef.value?.contains(prevActive))) prevActive.focus()
      else if (parent) (parent.nodes()[0] || parent.containerRef.value)?.focus()
    }
    prevActive = null
  }

  // 不用 immediate：setup 期求值 getter 可能碰到尚未初始化的 ref（TDZ 会崩整页）
  watch(active, (v) => { v ? activate() : deactivate() })
  onMounted(() => { if (active.value) activate() })
  onUnmounted(deactivate)
  return { isTop: () => traps.at(-1) === trap }
}
