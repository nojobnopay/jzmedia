import { existsSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { docsRoot, publicPages, walkFiles } from '../.vitepress/public-pages.mjs'
import { checkDiagrams } from '../.vitepress/diagram-source.mjs'
import { assertPublicOutput, collectScriptHashes } from './artifacts.mjs'
import { attr, elements, textContent } from './html.mjs'
import { defaultDist } from './server.mjs'
import { site, normalizeBase } from './site-config.mjs'

export function checkOutput(root = defaultDist, base = site.base) {
  base = normalizeBase(base)
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
      const resources = ['src', 'poster', 'href', 'xlink:href'].map(name => attr(node, name))
      // A picture's mobile source may never load during a desktop-only review.
      // Inspect every local srcset candidate as well as the fallback img src.
      if (attr(node, 'srcset')) resources.push(...attr(node, 'srcset').split(',').map(candidate => candidate.trim().split(/\s+/)[0]))
      for (const raw of resources) {
        if (!raw || /^(?:data:|blob:|mailto:|tel:)/i.test(raw)) continue
        let target
        try { target = new URL(raw, `http://docs.invalid${base}${file}`) }
        catch { errors.push(`${file}: 无效资源地址 ${raw}`); continue }
        if (target.origin !== 'http://docs.invalid') {
          if (node.tagName !== 'a') errors.push(`${file}: 离线文档依赖外部资源 ${raw}`)
          continue
        }
        if (!target.pathname.startsWith(base)) {
          errors.push(`${file}: 链接离开帮助站 ${raw}`); continue
        }
        let rel = decodeURIComponent(target.pathname.slice(base.length))
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

export function checkAppHelpLinks(root = docsRoot, dist = defaultDist) {
  const frontend = path.resolve(root, '../frontend/src')
  if (!existsSync(frontend)) return []
  const errors = []
  for (const file of walkFiles(frontend).filter(file => /\.(?:vue|js)$/.test(file))) {
    const source = readFileSync(path.join(frontend, file), 'utf8')
    for (const match of source.matchAll(/<HelpLink\b[^>]*?\bpage=["']([^"']+)["']/g)) {
      const slug = match[1].replace(/^\/?help\//, '').replace(/^\//, '').split('#')[0]
      const target = slug ? (slug.endsWith('.html') ? slug : `${slug}.html`) : 'index.html'
      if (!existsSync(path.join(dist, target))) errors.push(`frontend/src/${file}: 帮助入口不存在 ${match[1]}`)
    }
  }
  return errors
}

// Storyboard images live in the generated theme module rather than Markdown
// attributes. Read its editable source so the Node checker does not import
// Vite's ?url modules, and validate every step, even the initially hidden ones.
function storyboardAssets(root) {
  const assets = new Map()
  const errors = []
  const assetRoot = path.resolve(root, 'assets')
  let stories
  let loaded = false
  for (const file of publicPages(root)) {
    const source = readFileSync(path.join(root, file), 'utf8').replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm, '')
    for (const match of source.matchAll(/<DocStoryboard\b[^>]*>/g)) {
      if (!loaded) {
        loaded = true
        try { stories = JSON.parse(readFileSync(path.resolve(root, '../design/doc-storyboards.json'), 'utf8')).stories }
        catch { errors.push('design/doc-storyboards.json: 分镜源不存在或不是有效 JSON') }
      }
      const id = match[0].match(/\bstory=["']([^"']+)["']/)?.[1]
      if (!id) { errors.push(`${file}: 分镜组件需要固定 story 标识`); continue }
      if (!stories || typeof stories !== 'object' || !Object.hasOwn(stories, id)) {
        errors.push(`${file}: 未知分镜 ${id}`)
        continue
      }
      const story = stories[id]
      const frames = story?.frames
      if (!Array.isArray(frames) || !frames.length) { errors.push(`${file}: 分镜 ${id} 缺少画面`); continue }
      function addAsset(name, label) {
        if (typeof name !== 'string' || !name || path.isAbsolute(name) ||
            /^(?:[a-z]+:)?\/\//i.test(name) || name.split(/[\\/]/).includes('..')) {
          errors.push(`${file}: 分镜 ${id} ${label}必须使用 assets 内的本地素材`)
          return
        }
        const target = path.resolve(assetRoot, name)
        if (!target.startsWith(assetRoot + path.sep)) {
          errors.push(`${file}: 分镜 ${id} 素材离开 assets 目录 ${name}`)
          return
        }
        const rel = path.relative(assetRoot, target).split(path.sep).join('/')
        assets.set(rel, file)
        if (!existsSync(target)) errors.push(`${file}: 分镜素材不存在 assets/${rel}`)
      }
      if (story.illustration) {
        const illustration = story.illustration
        addAsset(illustration.file, '桌面插图')
        addAsset(illustration.mobileFile, '手机插图')
        if (typeof illustration.alt !== 'string' || !illustration.alt.trim()) errors.push(`${file}: 分镜 ${id} 插图需要 alt`)
        for (const name of ['width', 'height', 'mobileWidth', 'mobileHeight']) {
          if (!Number.isInteger(illustration[name]) || illustration[name] <= 0) errors.push(`${file}: 分镜 ${id} 插图 ${name} 必须是正整数`)
        }
      }
      const hasText = value => typeof value === 'string' && !!value.trim()
      const treeComplete = tree => tree && hasText(tree.root) && hasText(tree.season) &&
        Array.isArray(tree.files) && tree.files.length > 0 && tree.files.every(hasText)
      if (!hasText(story.title)) errors.push(`${file}: 分镜 ${id} 需要标题`)
      if (!['binding', 'tree', 'casting'].includes(story.kind)) errors.push(`${file}: 分镜 ${id} 类型无效`)
      if (story.kind === 'casting' && !story.illustration) errors.push(`${file}: 分镜 ${id} 缺少设备插图`)
      for (const [index, frame] of frames.entries()) {
        const step = `${file}: 分镜 ${id} 第 ${index + 1} 步`
        for (const name of ['label', 'heading', 'summary', 'detail']) {
          if (!hasText(frame?.[name])) errors.push(`${step}缺少 ${name} 说明`)
        }
        if (story.kind === 'casting' && (!Array.isArray(frame?.participants) || !frame.participants.length || frame.participants.some(participant =>
          !participant || !hasText(participant.name) || !hasText(participant.action)))) {
          errors.push(`${step}需要参与端与动作说明`)
        }
        if (story.kind === 'binding') {
          if (!['review', 'assign', 'saved'].includes(frame?.state)) errors.push(`${step}归属状态无效`)
          if (!Array.isArray(frame?.folders) || !frame.folders.length || !Array.isArray(frame?.targets) ||
              frame.targets.length !== frame.folders.length || frame.folders.some(folder => !hasText(folder?.name) || !hasText(folder?.file)) ||
              frame.targets.some(target => !hasText(target?.name) || !hasText(target?.detail))) {
            errors.push(`${step}需要成对的目录与归属说明`)
          }
        }
        if (story.kind === 'tree') {
          if (!['before', 'preview', 'after'].includes(frame?.state)) errors.push(`${step}整理状态无效`)
          if (!treeComplete(frame) || (frame.state === 'preview' && !treeComplete(frame.original))) {
            errors.push(`${step}需要完整的目录与文件；预览还需原目录`)
          }
        }
      }
    }
  }
  return { assets, errors }
}

export function checkSources(root = docsRoot) {
  const errors = storyboardAssets(root).errors
  for (const file of publicPages(root)) {
    const source = readFileSync(path.join(root, file), 'utf8').replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm, '')
    for (const match of source.matchAll(/<Doc(?:Figure|Video|Diagram)\b[^>]*>/g)) {
      const tag = match[0]
      for (const asset of tag.matchAll(/\b(?:src|mobile-src|poster|captions)="([^"]+)"/g)) {
        if (/^(https?:)?\/\//.test(asset[1])) errors.push(`${file}: 组件素材必须本地保存 ${asset[1]}`)
        else if (!existsSync(path.resolve(path.dirname(path.join(root, file)), asset[1]))) errors.push(`${file}: 组件素材不存在 ${asset[1]}`)
      }
      if (/^<Doc(?:Figure|Diagram)\b/.test(tag) && !/\balt="[^"]+"/.test(tag)) errors.push(`${file}: 图形组件需要 alt`)
    }
  }
  return errors
}

export function checkAssetManifest(root = docsRoot) {
  const { assets: referenced, errors } = storyboardAssets(root)
  const assetRoot = path.resolve(root, 'assets')
  for (const file of publicPages(root)) {
    const source = readFileSync(path.join(root, file), 'utf8').replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm, '')
    for (const match of source.matchAll(/<Doc(?:Figure|Video|Diagram)\b[^>]*>/g)) {
      for (const asset of match[0].matchAll(/\b(?:src|mobile-src|poster|captions)=["']([^"']+)["']/g)) {
        const raw = asset[1].split(/[?#]/)[0]
        if (/^(?:https?:)?\/\//.test(raw)) continue
        const target = raw.startsWith('/') ? path.join(root, raw) : path.resolve(root, path.dirname(file), raw)
        if (target.startsWith(assetRoot + path.sep)) referenced.set(path.relative(assetRoot, target).split(path.sep).join('/'), file)
      }
    }
  }
  if (!referenced.size) return errors
  let manifest
  try { manifest = JSON.parse(readFileSync(path.join(assetRoot, 'manifest.json'), 'utf8')) }
  catch { return [...errors, 'assets/manifest.json: 素材清单不存在或不是有效 JSON'] }
  if (!Array.isArray(manifest.assets)) return [...errors, 'assets/manifest.json: 缺少 assets 列表']
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
