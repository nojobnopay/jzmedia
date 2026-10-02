import { createServer } from 'node:http'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { chromium } from 'playwright'
import { docsRoot } from '../.vitepress/public-pages.mjs'
import { diagrams, checkDiagrams } from '../.vitepress/diagram-source.mjs'

if (process.argv.includes('--check')) {
  checkDiagrams()
  console.log(`流程图校验通过（${diagrams().length} 个 SVG）`)
} else {
  const sources = diagrams()
  const dir = path.join(docsRoot, '.vitepress/diagrams')
  mkdirSync(dir, { recursive: true })
  const pending = sources.filter(({ hash }) => !existsSync(path.join(dir, `${hash}.svg`)))
  if (!pending.length) console.log(`流程图均已生成（${sources.length} 个 SVG）`)
  else {
    const require = createRequire(import.meta.url)
    const mermaidDir = path.dirname(require.resolve('mermaid/package.json'))
    const server = createServer((request, response) => {
      if (request.url === '/') {
        response.setHeader('Content-Type', 'text/html; charset=utf-8')
        response.end('<!doctype html><html lang="zh-CN"><body><script type="module">import mermaid from "/dist/mermaid.esm.min.mjs";mermaid.initialize({startOnLoad:false,securityLevel:"strict",theme:"neutral",fontFamily:"sans-serif",flowchart:{htmlLabels:false}});window.mermaid=mermaid;</script></body></html>')
        return
      }
      const file = path.resolve(mermaidDir, `.${new URL(request.url, 'http://localhost').pathname}`)
      if (!file.startsWith(mermaidDir + path.sep) || !existsSync(file)) { response.writeHead(404).end(); return }
      response.setHeader('Content-Type', 'text/javascript; charset=utf-8')
      response.end(readFileSync(file))
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    let browser
    try {
      browser = await chromium.launch({ headless: true })
      const page = await browser.newPage()
      await page.goto(`http://127.0.0.1:${server.address().port}/`)
      await page.waitForFunction(() => !!window.mermaid)
      for (const source of pending) {
        const svg = await page.evaluate(async ({ code, hash }) =>
          (await window.mermaid.render(`graph${hash}`, code)).svg, source)
        writeFileSync(path.join(dir, `${source.hash}.svg`), svg + '\n')
        console.log(`生成 ${source.page} → ${source.hash}.svg`)
      }
    } finally {
      await browser?.close()
      await new Promise(resolve => server.close(resolve))
    }
  }
}
