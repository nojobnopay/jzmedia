import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import MiniSearch from 'minisearch'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { docsRoot, isPublicPage, publicPages, walkFiles } from '../.vitepress/public-pages.mjs'
import { tokenize, searchOptions } from '../.vitepress/search.mjs'
import { collectScriptHashes, assertPublicOutput } from '../scripts/artifacts.mjs'
import { checkAppHelpLinks, checkAssetManifest, checkOutput } from '../scripts/check.mjs'
import { defaultDist } from '../scripts/server.mjs'

test('publication is an explicit allowlist including compatibility pages', () => {
  assert.ok(isPublicPage('user-guide/movies.md'))
  assert.ok(isPublicPage('developer/architecture.md'))
  for (const file of ['private/secret.md', 'assets/README.md', 'roadmap/backlog.md',
    'developer/new-private-report.md', 'user-guide/new-draft.md']) {
    assert.equal(isPublicPage(file), false, file)
  }
})

test('Chinese words and Latin tokens use the same index/query implementation', () => {
  assert.deepEqual(tokenize('添加电影 字幕延迟 NAS SMB'), ['添加', '电影', '字幕', '延迟', 'nas', 'smb'])
  const index = new MiniSearch({ fields: ['text'], tokenize, searchOptions })
  index.addAll([{ id: 1, text: '设置中可以调整字幕延迟。' }, { id: 2, text: 'NAS 连接测试' }])
  assert.equal(index.search('字幕延迟')[0].id, 1)
  assert.equal(index.search('nas')[0].id, 2)
  const roundTrip = MiniSearch.loadJSON(JSON.stringify(index), { fields: ['text'], tokenize, searchOptions })
  assert.equal(roundTrip.search('字幕延迟')[0].id, 1)
})

test('the built local index finds real user tasks and excludes unpublished sources', async () => {
  const indexFile = walkFiles(defaultDist).find(file => file.includes('@localSearchIndexroot.') && file.endsWith('.js'))
  assert.ok(indexFile, 'Run npm run build before testing the published search index')
  const { default: json } = await import(pathToFileURL(path.join(defaultDist, indexFile)).href)
  const index = MiniSearch.loadJSON(json, { fields: ['title', 'titles', 'text'], storeFields: ['title', 'titles'], tokenize, searchOptions })
  for (const query of ['添加电影', '字幕延迟', '没有声音', 'NAS']) assert.ok(index.search(query).length, query)
  const ids = Object.values(JSON.parse(json).documentIds)
  assert.ok(ids.length > 20)
  assert.ok(ids.every(id => !/private|roadmap|assets\/README/.test(id)))
})

test('the build contains only public HTML and has exactly its final script hashes', () => {
  assertPublicOutput(defaultDist, publicPages())
  const manifest = JSON.parse(readFileSync(path.join(defaultDist, 'csp-hashes.json'), 'utf8'))
  assert.deepEqual(manifest.scriptHashes, collectScriptHashes(defaultDist))
  assert.ok(manifest.scriptHashes.length > 0)
  for (const page of publicPages()) {
    assert.doesNotMatch(readFileSync(path.join(defaultDist, page.replace(/\.md$/, '.html')), 'utf8'), /_vp-fn_|new Function/, page)
  }
})

test('app help slugs are validated as the actual .html interface', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jzmedia-help-links-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(path.join(root, 'docs/.vitepress/dist/user-guide'), { recursive: true })
  mkdirSync(path.join(root, 'frontend/src'), { recursive: true })
  writeFileSync(path.join(root, 'docs/.vitepress/dist/user-guide/subtitles.html'), '<h1>字幕</h1>')
  writeFileSync(path.join(root, 'frontend/src/Player.vue'), '<HelpLink page="user-guide/subtitles" />')
  assert.deepEqual(checkAppHelpLinks(path.join(root, 'docs')), [])
  writeFileSync(path.join(root, 'frontend/src/Player.vue'), '<HelpLink page="user-guide/missing" />')
  assert.match(checkAppHelpLinks(path.join(root, 'docs'))[0], /帮助入口不存在/)
})

test('HTML checking catches broken component assets, anchors, and external media', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jzmedia-doc-html-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  writeFileSync(path.join(root, 'index.html'), '<h1>帮助</h1><img src="missing.webp" alt="截图"><a href="#missing">跳转</a><video src="https://example.com/demo.mp4"></video>')
  const errors = checkOutput(root).join('\n')
  assert.match(errors, /缺失目标/)
  assert.match(errors, /不存在锚点/)
  assert.match(errors, /离线文档依赖外部资源/)
  assert.match(errors, /视频缺少字幕轨/)
})

test('active tutorial assets require matching provenance records without inventing a new review date', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jzmedia-doc-assets-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(path.join(root, 'user-guide'))
  mkdirSync(path.join(root, 'assets'))
  writeFileSync(path.join(root, 'user-guide/onboarding.md'), `<DocFigure src="../assets/still.webp" alt="截图" />
<DocVideo src="../assets/demo.mp4" poster="../assets/still.webp" captions="../assets/demo.vtt" />
<DocDiagram src="../assets/flow.svg" alt="概念图" />
<DocDiagram src="/.vitepress/diagrams/generated.svg" alt="缓存图" />
\`\`\`html
<DocFigure src="../assets/example.webp" alt="代码示例" />
\`\`\`
`)
  const names = ['still.webp', 'demo.mp4', 'demo.vtt', 'flow.svg']
  const assets = names.map(file => {
    const bytes = Buffer.from(file)
    writeFileSync(path.join(root, 'assets', file), bytes)
    return { file, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), verified_at: '2026-09-28' }
  })
  const save = entries => writeFileSync(path.join(root, 'assets/manifest.json'), JSON.stringify({ assets: entries }))
  save(assets)
  assert.deepEqual(checkAssetManifest(root), [])
  save(assets.filter(entry => entry.file !== 'demo.vtt'))
  assert.match(checkAssetManifest(root).join('\n'), /组件素材未登记 assets\/demo.vtt/)
  save(assets)
  writeFileSync(path.join(root, 'assets/still.webp'), 'still.WEBP') // Same length, different bytes.
  assert.match(checkAssetManifest(root).join('\n'), /SHA-256 与清单不符 assets\/still.webp/)
  writeFileSync(path.join(root, 'assets/still.webp'), 'still.webp')
  save(assets.map(entry => entry.file === 'demo.mp4' ? { ...entry, bytes: 0 } : entry))
  assert.match(checkAssetManifest(root).join('\n'), /字节数或 SHA-256 与清单不符 assets\/demo.mp4/)
})

test('figure, steps, and captioned video render usable content without a browser', async t => {
  const tempBase = path.join(docsRoot, '.vitepress/.temp')
  mkdirSync(tempBase, { recursive: true })
  const dir = mkdtempSync(path.join(tempBase, 'components-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  async function loadComponent(name) {
    const source = readFileSync(path.join(docsRoot, '.vitepress/theme/components', `${name}.vue`), 'utf8')
    const { descriptor } = parse(source)
    const { content } = compileScript(descriptor, { id: name, inlineTemplate: true })
    const file = path.join(dir, `${name}.mjs`)
    writeFileSync(file, content)
    return (await import(pathToFileURL(file).href)).default
  }
  const [Figure, Step, Video] = await Promise.all(['DocFigure', 'DocStep', 'DocVideo'].map(loadComponent))
  const html = await renderToString(createSSRApp({ render() { return h('div', [
    h(Step, { number: 1, title: '选择视频库' }, { default: () => h('p', '点击检查并使用此视频库'), image: () => h(Figure, { src: '/help/example.webp', alt: '检查连接按钮', caption: '隔离演示', marks: [{ x: 25, y: 50, label: '1' }] }) }),
    h(Video, { src: '/help/demo.mp4', poster: '/help/poster.webp', captions: '/help/demo.vtt', title: '扫描演示' }, { default: () => h('p', '扫描后核对已登记数量') }),
  ]) } }))
  assert.match(html, /检查并使用此视频库/)
  assert.match(html, /has-image/)
  assert.match(html, /aria-label="放大图片：检查连接按钮"/)
  assert.match(html, /left:clamp\(16px, 25%, calc\(100% - 16px\)\)/)
  assert.match(html, /preload="none"/)
  assert.match(html, /kind="captions"/)
  assert.match(html, /扫描后核对已登记数量/)
  assert.doesNotMatch(html, /autoplay/)
})
