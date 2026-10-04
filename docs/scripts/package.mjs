import { cpSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { docsRoot } from '../.vitepress/public-pages.mjs'
import { appSite } from './site-config.mjs'

// The offline package always uses the app layout, even in a shell previously
// used to preview Pages. Build and check that exact output before copying it.
for (const command of ['build', 'check']) {
  const result = spawnSync('npm', ['run', command], {
    cwd: docsRoot, stdio: 'inherit', env: { ...process.env, JZMEDIA_DOCS_TARGET: 'app' },
  })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status || 1)
}

const output = path.join(docsRoot, '.artifacts/jzmedia-help')
rmSync(output, { recursive: true, force: true })
mkdirSync(output, { recursive: true })
cpSync(appSite.outDir, path.join(output, 'help'), { recursive: true,
  filter: source => !path.basename(source).startsWith('.') })
writeFileSync(path.join(output, 'index.html'), '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/help/"><title>jzmedia 帮助</title><a href="/help/">打开 jzmedia 帮助</a></html>\n')
console.log(`独立站产物：${output}（根目录跳转 /help/；通过 HTTP 静态服务器发布）`)
