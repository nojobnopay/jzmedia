import { readFileSync } from 'node:fs'
import path from 'node:path'
import { docsRoot } from '../.vitepress/public-pages.mjs'

// Build tools share one target so validation and preview cannot read the other
// build's output. App builds always retain the URL expected by FastAPI.
export function normalizeBase(value) {
  if (typeof value !== 'string' || !/^\/(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]*$/.test(value) ||
      value.split('/').some(part => part === '.' || part === '..')) {
    throw new Error('JZMEDIA_DOCS_BASE 必须是站内绝对路径，例如 /jzmedia/ 或 /')
  }
  return value.endsWith('/') ? value : `${value}/`
}

export function resolveSite(env = process.env) {
  const target = env.JZMEDIA_DOCS_TARGET || 'app'
  if (!['app', 'pages'].includes(target)) throw new Error('JZMEDIA_DOCS_TARGET 仅支持 app 或 pages')
  return {
    target,
    base: target === 'app' ? '/help/' : normalizeBase(env.JZMEDIA_DOCS_BASE ?? '/jzmedia/'),
    outDir: path.join(docsRoot, target === 'app' ? '.vitepress/dist' : '.artifacts/pages'),
  }
}

export const site = resolveSite()
export const appSite = resolveSite({})
export const repositoryUrl = 'https://github.com/nojobnopay/jzmedia'

export function buildVersion() {
  const source = readFileSync(path.resolve(docsRoot, '../version.properties'), 'utf8')
  const version = source.match(/^versionName=(\d+\.\d+\.\d+)\s*$/m)?.[1]
  if (!version) throw new Error('version.properties 缺少有效的 versionName')
  return version
}
