import { existsSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { docsRoot, publicPages, walkFiles } from '../.vitepress/public-pages.mjs'
import { checkDiagrams } from '../.vitepress/diagram-source.mjs'
import { assertPublicOutput, collectScriptHashes } from './artifacts.mjs'
import { attr, elements, textContent } from './html.mjs'
import { defaultDist } from './server.mjs'

export function checkOutput(root = defaultDist) {
  const errors = []
  const pages = walkFiles(root).filter(file => file.endsWith('.html'))
  const nodes = new Map(pages.map(file => [file, elements(readFileSync(path.join(root, file), 'utf8'))]))
  const ids = new Map([...nodes].map(([file, items]) => [file, new Set(items.map(node => attr(node, 'id')).filter(Boolean))]))
  for (const [file, items] of nodes) {
    for (const node of items) {
      if (node.tagName === 'img' && !attr(node, 'alt')?.trim()) errors.push(`${file}: 图片缺少替代文字`)
      if (node.tagName === 'video') {
        if (attr(node, 'preload') !== 'none') errors.push(`${file}: 视频应 preload="none"`)
        if (attr(node, 'autoplay') !== undefined) errors.push(`${file}: 视频不应自动播放`)
        if (!node.childNodes?.some(child => child.tagName === 'track' && attr(child, 'kind') === 'captions')) errors.push(`${file}: 视频缺少字幕轨`)
      }
      const attributes = ['src', 'poster', 'href', 'xlink:href']
      for (const name of attributes) {
        const raw = attr(node, name)
        if (!raw || /^(?:data:|blob:|mailto:|tel:)/i.test(raw)) continue
        let target
        try { target = new URL(raw, `http://docs.invalid/help/${file}`) }
        catch { errors.push(`${file}: 无效资源地址 ${raw}`); continue }
        if (target.origin !== 'http://docs.invalid') {
          if (node.tagName !== 'a') errors.push(`${file}: 离线文档依赖外部资源 ${raw}`)
          continue
        }
        if (!target.pathname.startsWith('/help/')) {
          errors.push(`${file}: 链接离开帮助站 ${raw}`); continue
        }
        let rel = decodeURIComponent(target.pathname.slice(6))
        if (!rel || rel.endsWith('/')) rel += 'index.html'
        const dest = path.resolve(root, rel)
        if (!dest.startsWith(root + path.sep) || !existsSync(dest)) {
          errors.push(`${file}: 缺失目标 ${raw}`); continue
        }
        if (target.hash && rel.endsWith('.html') && !ids.get(rel)?.has(decodeURIComponent(target.hash.slice(1)))) {
          errors.push(`${file}: 不存在锚点 ${raw}`)
        }
      }
    }
    if (file !== '404.html' && !items.some(node => node.tagName === 'h1' && textContent(node).trim())) errors.push(`${file}: 缺少服务端渲染标题与正文`)
  }
  return errors
}

export function checkAppHelpLinks(root = docsRoot) {
  const frontend = path.resolve(root, '../frontend/src')
  if (!existsSync(frontend)) return []
  const errors = []
  for (const file of walkFiles(frontend).filter(file => /\.(?:vue|js)$/.test(file))) {
    const source = readFileSync(path.join(frontend, file), 'utf8')
    for (const match of source.matchAll(/<HelpLink\b[^>]*?\bpage=["']([^"']+)["']/g)) {
      const slug = match[1].replace(/^\/?help\//, '').replace(/^\//, '').split('#')[0]
      const target = slug ? (slug.endsWith('.html') ? slug : `${slug}.html`) : 'index.html'
      if (!existsSync(path.join(root, '.vitepress/dist', target))) errors.push(`frontend/src/${file}: 帮助入口不存在 ${match[1]}`)
    }
  }
  return errors
}

export function checkSources(root = docsRoot) {
  const errors = []
  for (const file of publicPages(root)) {
    const source = readFileSync(path.join(root, file), 'utf8').replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm, '')
    for (const match of source.matchAll(/<Doc(?:Figure|Video|Diagram)\b[^>]*>/g)) {
      const tag = match[0]
      for (const asset of tag.matchAll(/\b(?:src|poster|captions)="([^"]+)"/g)) {
        if (/^(https?:)?\/\//.test(asset[1])) errors.push(`${file}: 组件素材必须本地保存 ${asset[1]}`)
        else if (!existsSync(path.resolve(path.dirname(path.join(root, file)), asset[1]))) errors.push(`${file}: 组件素材不存在 ${asset[1]}`)
      }
      if (/^<Doc(?:Figure|Diagram)\b/.test(tag) && !/\balt="[^"]+"/.test(tag)) errors.push(`${file}: 图形组件需要 alt`)
    }
  }
  return errors
}

export function checkAssetManifest(root = docsRoot) {
  const referenced = new Map()
  const assetRoot = path.resolve(root, 'assets')
  for (const file of publicPages(root)) {
    const source = readFileSync(path.join(root, file), 'utf8').replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm, '')
    for (const match of source.matchAll(/<Doc(?:Figure|Video|Diagram)\b[^>]*>/g)) {
      for (const asset of match[0].matchAll(/\b(?:src|poster|captions)=["']([^"']+)["']/g)) {
        const raw = asset[1].split(/[?#]/)[0]
        if (/^(?:https?:)?\/\//.test(raw)) continue
        const target = raw.startsWith('/') ? path.join(root, raw) : path.resolve(root, path.dirname(file), raw)
        if (target.startsWith(assetRoot + path.sep)) referenced.set(path.relative(assetRoot, target).split(path.sep).join('/'), file)
      }
    }
  }
  if (!referenced.size) return []
  let manifest
  try { manifest = JSON.parse(readFileSync(path.join(assetRoot, 'manifest.json'), 'utf8')) }
  catch { return ['assets/manifest.json: 素材清单不存在或不是有效 JSON'] }
  if (!Array.isArray(manifest.assets)) return ['assets/manifest.json: 缺少 assets 列表']
  const errors = []
  const entries = new Map()
  for (const entry of manifest.assets) {
    if (!entry || typeof entry.file !== 'string') { errors.push('assets/manifest.json: 素材记录缺少 file'); continue }
    if (entries.has(entry.file)) errors.push(`assets/manifest.json: 重复素材记录 ${entry.file}`)
    entries.set(entry.file, entry)
  }
  for (const [file, page] of referenced) {
    const entry = entries.get(file)
    if (!entry) { errors.push(`${page}: 组件素材未登记 assets/${file}`); continue }
    if (!existsSync(path.join(assetRoot, file))) { errors.push(`${page}: 清单素材不存在 assets/${file}`); continue }
    const bytes = readFileSync(path.join(assetRoot, file))
    if (entry.bytes !== bytes.length || entry.sha256 !== createHash('sha256').update(bytes).digest('hex')) {
      errors.push(`${page}: 素材字节数或 SHA-256 与清单不符 assets/${file}；请通过对应拍摄或生成流程登记来源`)
    }
  }
  return errors
}

export function checkAll() {
  checkDiagrams()
  assertPublicOutput(defaultDist, publicPages())
  const actual = JSON.parse(readFileSync(path.join(defaultDist, 'csp-hashes.json'), 'utf8')).scriptHashes
  const expected = collectScriptHashes(defaultDist)
  const errors = [...checkSources(), ...checkAssetManifest(), ...checkOutput(), ...checkAppHelpLinks()]
  if (JSON.stringify(actual) !== JSON.stringify(expected)) errors.push('CSP 哈希与最终 HTML 不一致')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(`文档检查通过：${publicPages().length} 页；链接、锚点、素材与清单、公开边界、SSR、CSP 与应用帮助入口有效。`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) checkAll()
