// 弹窗焦点陷阱（评审 R14-B6）：Tab/Shift+Tab 在弹窗内循环，打开聚焦、关闭还原。
// 用法：const box = ref(null); useFocusTrap(openRef, box)
import { nextTick, onUnmounted, watch } from 'vue'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function useFocusTrap(active, containerRef) {
  let prevActive = null

  function nodes() {
    const root = containerRef.value
    if (!root) return []
    return [...root.querySelectorAll(FOCUSABLE)]
      .filter(n => n.offsetWidth > 0 || n.offsetHeight > 0)
  }

  function onKeydown(e) {
    if (e.key !== 'Tab' || !active.value) return
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
    prevActive = document.activeElement
    document.addEventListener('keydown', onKeydown, true)
    nextTick(() => {
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
    try { if (prevActive && prevActive.focus) prevActive.focus() } catch (e) { /* 忽略 */ }
    prevActive = null
  }

  watch(active, (v) => { v ? activate() : deactivate() }, { immediate: true })
  onUnmounted(deactivate)
}
