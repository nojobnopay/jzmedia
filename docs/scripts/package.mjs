import { cpSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { docsRoot } from '../.vitepress/public-pages.mjs'
import { defaultDist } from './server.mjs'

const output = path.join(docsRoot, '.artifacts/jzmedia-help')
rmSync(output, { recursive: true, force: true })
mkdirSync(output, { recursive: true })
cpSync(defaultDist, path.join(output, 'help'), { recursive: true,
  filter: source => !path.basename(source).startsWith('.') })
writeFileSync(path.join(output, 'index.html'), '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/help/"><title>jzmedia 帮助</title><a href="/help/">打开 jzmedia 帮助</a></html>\n')
console.log(`独立站产物：${output}（根目录跳转 /help/；通过 HTTP 静态服务器发布）`)
