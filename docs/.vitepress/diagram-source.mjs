import { createHash } from 'node:crypto'
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { docsRoot, publicPages } from './public-pages.mjs'

export function diagramHash(source) {
  return createHash('sha256').update(source.trim()).digest('hex').slice(0, 20)
}

export function diagrams(root = docsRoot) {
  const found = new Map()
  for (const page of publicPages(root)) {
    const source = readFileSync(path.join(root, page), 'utf8')
    for (const match of source.matchAll(/^```mermaid\s*\n([\s\S]*?)^```\s*$/gm)) {
      const code = match[1].trim()
      const hash = diagramHash(code)
      found.set(hash, { hash, code, page })
    }
  }
  return [...found.values()]
}

export function checkDiagrams(root = docsRoot) {
  const missing = diagrams(root).filter(({ hash }) =>
    !existsSync(path.join(root, '.vitepress/diagrams', `${hash}.svg`)))
  if (missing.length) throw new Error(`流程图需要更新：${missing.map(item => item.page).join('、')}。请在 docs/ 运行 npm run diagrams。`)
}

export function mermaidFence(md) {
  const original = md.renderer.rules.fence
  md.renderer.rules.fence = (tokens, index, options, env, self) => {
    const token = tokens[index]
    if (token.info.trim() !== 'mermaid') return original(tokens, index, options, env, self)
    return `<DocDiagram src="/.vitepress/diagrams/${diagramHash(token.content)}.svg" alt="流程图，说明见本节正文" />\n`
  }
}
