import { createHash } from 'node:crypto'
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { htmlPath, walkFiles } from '../.vitepress/public-pages.mjs'
import { inlineScripts } from './html.mjs'

export const compatibilityFiles = ['assets/previews/onboarding/index.html']

// VitePress 1.6 serializes custom search functions into its HTML metadata and
// restores them with new Function. Publish data only; the theme imports the same
// tokenizer statically, keeping the application's existing CSP intact.
export function staticMetadata(html, _id, context) {
  if (!html.includes('_vp-fn_')) return html
  const data = JSON.stringify({ ...context.siteConfig.site, head: [] }, (key, value) =>
    key.startsWith('_') || typeof value === 'function' ? undefined : value).replace(/</g, '\\u003c')
  const result = html.replace(
    /<script>(window\.__VP_HASH_MAP__=[\s\S]*?;)function deserializeFunctions[\s\S]*?<\/script>/,
    (_script, hashMap) => `<script>${hashMap}window.__VP_SITE_DATA__=${data};</script>`)
  if (result.includes('_vp-fn_')) throw new Error('VitePress metadata format changed; review the CSP-safe search integration')
  return result
}

export function collectScriptHashes(root) {
  const hashes = new Set()
  for (const file of walkFiles(root).filter(file => file.endsWith('.html'))) {
    for (const script of inlineScripts(readFileSync(path.join(root, file), 'utf8'))) {
      hashes.add(`sha256-${createHash('sha256').update(script).digest('base64')}`)
    }
  }
  return [...hashes].sort()
}

export function assertPublicOutput(root, pages) {
  const expected = new Set([...pages.map(htmlPath), ...compatibilityFiles, '404.html'])
  const actual = new Set(walkFiles(root).filter(file => file.endsWith('.html')))
  const missing = [...expected].filter(file => !actual.has(file))
  const unexpected = [...actual].filter(file => !expected.has(file))
  if (missing.length || unexpected.length) {
    throw new Error(`发布页面不一致：缺少 [${missing}]；意外输出 [${unexpected}]`)
  }
}

export function finishBuild(config) {
  for (const file of compatibilityFiles) {
    const dest = path.join(config.outDir, file)
    mkdirSync(path.dirname(dest), { recursive: true })
    copyFileSync(path.join(config.srcDir, file), dest)
  }
  assertPublicOutput(config.outDir, config.pages)
  writeFileSync(path.join(config.outDir, 'csp-hashes.json'),
    JSON.stringify({ scriptHashes: collectScriptHashes(config.outDir) }, null, 2) + '\n')
}
