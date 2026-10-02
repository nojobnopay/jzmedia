import { parse } from 'parse5'

export function elements(html) {
  const result = []
  function visit(node) {
    if (node.tagName) result.push(node)
    for (const child of node.childNodes || []) visit(child)
  }
  visit(parse(html))
  return result
}

export function attr(node, name) { return node.attrs?.find(item => item.name === name)?.value }
export function textContent(node) { return node.value || (node.childNodes || []).map(textContent).join('') }

export function inlineScripts(html) {
  return elements(html).filter(node => node.tagName === 'script' && !attr(node, 'src'))
    .map(textContent).filter(Boolean)
}
