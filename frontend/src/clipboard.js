export async function copyText(text) {
  const s = String(text ?? '')
  if (!s) return false
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(s)
      return true
    } catch (e) { }
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = s
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    const host = document.fullscreenElement || document.body
    host.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, s.length)
    const ok = document.execCommand('copy')
    host.removeChild(ta)
    return ok
  } catch (e) {
    return false
  }
}
