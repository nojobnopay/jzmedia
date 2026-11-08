import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { checkAssetManifest, checkOutput, checkSources } from '../scripts/check.mjs'

function fixture(t) {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'jzmedia-storyboards-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const root = path.join(dir, 'docs')
  const design = path.join(dir, 'design/doc-storyboards.json')
  for (const subdir of ['docs/user-guide', 'docs/assets/illustrations', 'design']) mkdirSync(path.join(dir, subdir), { recursive: true })
  const stories = JSON.parse(readFileSync(new URL('../../design/doc-storyboards.json', import.meta.url), 'utf8')).stories
  const assets = []
  const pages = { 'tv-bindings': 'tv-bindings', 'tv-organizing': 'tv-organizing', casting: 'playback-controls' }
  for (const [id, page] of Object.entries(pages)) {
    writeFileSync(path.join(root, 'user-guide', `${page}.md`), `<DocStoryboard story="${id}" />`)
  }
  for (const file of [stories.casting.illustration.file, stories.casting.illustration.mobileFile]) {
    const bytes = Buffer.from(file)
    writeFileSync(path.join(root, 'assets', file), bytes)
    assets.push({ file, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), generated_at: '2026-10-01' })
  }
  const saveDesign = () => writeFileSync(design, JSON.stringify({ stories }))
  const saveManifest = (entries = assets) => writeFileSync(path.join(root, 'assets/manifest.json'), JSON.stringify({ assets: entries }))
  saveDesign()
  saveManifest()
  return { root, design, stories, assets, saveDesign, saveManifest }
}

test('responsive story content and both art-directed images validate with per-asset provenance', t => {
  const { root } = fixture(t)
  assert.deepEqual(checkSources(root), [])
  assert.deepEqual(checkAssetManifest(root), [])
})

test('noninitial HTML steps cannot silently lose their explanation or mapping', t => {
  const { root, stories, saveDesign } = fixture(t)
  stories['tv-bindings'].frames[2].detail = ' '
  stories['tv-bindings'].frames[1].targets.pop()
  saveDesign()
  const errors = checkSources(root).join('\n')
  assert.match(errors, /tv-bindings 第 3 步缺少 detail 说明/)
  assert.match(errors, /tv-bindings 第 2 步需要成对的目录与归属说明/)
})

test('mobile illustration must exist and retain matching provenance even when the desktop image is valid', t => {
  const { root, assets, stories, saveManifest } = fixture(t)
  const file = stories.casting.illustration.mobileFile
  saveManifest(assets.filter(entry => entry.file !== file))
  assert.match(checkAssetManifest(root).join('\n'), /组件素材未登记 assets\/illustrations\/casting-devices-mobile\.webp/)
  saveManifest()
  const target = path.join(root, 'assets', file)
  writeFileSync(target, readFileSync(target, 'utf8').replace('mobile', 'Mobile'))
  assert.match(checkAssetManifest(root).join('\n'), /SHA-256 与清单不符 assets\/illustrations\/casting-devices-mobile\.webp/)
  rmSync(target)
  assert.match(checkSources(root).join('\n'), /分镜素材不存在 assets\/illustrations\/casting-devices-mobile\.webp/)
})

test('storyboard typos and incomplete references fail before a blank story is published', t => {
  const { root } = fixture(t)
  const page = path.join(root, 'user-guide/tv-bindings.md')
  writeFileSync(page, '<DocStoryboard story="tv-binding" />')
  assert.match(checkSources(root).join('\n'), /未知分镜 tv-binding/)
  writeFileSync(page, '<DocStoryboard />')
  assert.match(checkSources(root).join('\n'), /需要固定 story 标识/)
  writeFileSync(page, '```html\n<DocStoryboard story="example" />\n```\n')
  assert.deepEqual(checkSources(root), [], 'fenced examples are not live component references')
})

test('directory previews require complete before and after paths and valid states', t => {
  const { root, stories, saveDesign } = fixture(t)
  stories['tv-organizing'].frames[1].original.files = []
  stories['tv-organizing'].frames[2].season = ''
  stories['tv-bindings'].frames[0].state = 'unknown'
  saveDesign()
  const errors = checkSources(root).join('\n')
  assert.match(errors, /tv-organizing 第 2 步需要完整的目录与文件/)
  assert.match(errors, /tv-organizing 第 3 步需要完整的目录与文件/)
  assert.match(errors, /tv-bindings 第 1 步归属状态无效/)
})

test('art-directed stories require a local mobile image and readable HTML step content', t => {
  const { root, stories, saveDesign } = fixture(t)
  stories.casting.illustration.file = 'https://example.com/desktop.webp'
  delete stories.casting.illustration.mobileFile
  stories.casting.illustration.alt = ' '
  stories.casting.illustration.mobileWidth = 0
  stories.casting.frames[1].summary = ''
  stories.casting.frames[2].participants = [{ name: '电视' }]
  saveDesign()
  const errors = checkSources(root).join('\n')
  assert.match(errors, /casting 桌面插图必须使用 assets 内的本地素材/)
  assert.match(errors, /casting 手机插图必须使用 assets 内的本地素材/)
  assert.match(errors, /casting 插图需要 alt/)
  assert.match(errors, /casting 插图 mobileWidth 必须是正整数/)
  assert.match(errors, /casting 第 2 步缺少 summary 说明/)
  assert.match(errors, /casting 第 3 步需要参与端与动作说明/)
  stories.casting.illustration.mobileFile = '../../private/mobile.webp'
  saveDesign()
  assert.match(checkSources(root).join('\n'), /casting 手机插图必须使用 assets 内的本地素材/)
})

test('published picture sources cannot hide missing, external or out-of-base mobile assets', t => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'jzmedia-picture-html-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  writeFileSync(path.join(root, 'desktop.webp'), 'desktop')
  writeFileSync(path.join(root, 'mobile.webp'), 'mobile')
  const save = source => writeFileSync(path.join(root, 'index.html'), `<h1>投屏</h1><picture><source media="(max-width: 640px)" srcset="${source}"><img src="desktop.webp" alt="设备示意"></picture>`)
  save('mobile.webp 1x, desktop.webp 2x')
  assert.deepEqual(checkOutput(root, '/help/'), [])
  save('mobile.webp 1x, missing-mobile.webp 2x')
  assert.match(checkOutput(root, '/help/').join('\n'), /缺失目标 missing-mobile\.webp/)
  save('https://example.com/mobile.webp')
  assert.match(checkOutput(root, '/help/').join('\n'), /离线文档依赖外部资源/)
  save('/mobile.webp')
  assert.match(checkOutput(root, '/help/').join('\n'), /链接离开帮助站/)
})

test('missing or malformed storyboard definitions cannot silently skip asset checks', t => {
  const { root, design, stories, saveDesign } = fixture(t)
  rmSync(design)
  assert.match(checkSources(root).join('\n'), /分镜源不存在或不是有效 JSON/)
  writeFileSync(design, '{')
  assert.match(checkAssetManifest(root).join('\n'), /分镜源不存在或不是有效 JSON/)
  stories.casting.frames = []
  saveDesign()
  assert.match(checkSources(root).join('\n'), /分镜 casting 缺少画面/)
})
