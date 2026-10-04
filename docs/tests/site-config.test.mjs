import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { normalizeBase, resolveSite, site, buildVersion, repositoryUrl } from '../scripts/site-config.mjs'
import { checkOutput } from '../scripts/check.mjs'
import { createHelpServer } from '../scripts/server.mjs'

test('app and Pages use separate outputs and app URLs ignore Pages configuration', () => {
  const app = resolveSite({ JZMEDIA_DOCS_BASE: '/another/' })
  const pages = resolveSite({ JZMEDIA_DOCS_TARGET: 'pages', JZMEDIA_DOCS_BASE: '/another' })
  assert.equal(app.base, '/help/')
  assert.equal(pages.base, '/another/')
  assert.notEqual(app.outDir, pages.outDir)
  assert.ok(app.outDir.endsWith('/.vitepress/dist'))
  assert.ok(pages.outDir.endsWith('/.artifacts/pages'))
  assert.throws(() => resolveSite({ JZMEDIA_DOCS_TARGET: 'typo' }), /仅支持/)
  for (const base of ['', 'help/', '//example.com/', '/a//b/', '/a/../b/', '/./', '/x?q=1', '/x#y', '/%2e%2e/']) {
    assert.throws(() => normalizeBase(base), /绝对路径/, base)
  }
})

for (const base of ['/help/', '/jzmedia/', '/forked-repo/', '/']) {
  test(`links and static preview work when mounted at ${base}`, async t => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'jzmedia-help-base-'))
    t.after(() => rmSync(root, { recursive: true, force: true }))
    mkdirSync(path.join(root, 'guide'))
    writeFileSync(path.join(root, 'index.html'), `<h1>帮助</h1><a href="${base}guide/start.html#read">开始</a><img src="${base}image.svg" alt="示例">`)
    writeFileSync(path.join(root, 'guide/start.html'), '<h1 id="read">教程</h1><a href="../index.html">返回</a>')
    writeFileSync(path.join(root, 'image.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>')
    writeFileSync(path.join(root, 'demo.mp4'), '0123456789')
    writeFileSync(path.join(root, '404.html'), '<h1>没有此页</h1>')
    writeFileSync(path.join(root, 'csp-hashes.json'), '{"scriptHashes":[]}')
    assert.deepEqual(checkOutput(root, base), [])
    if (base !== '/') {
      writeFileSync(path.join(root, 'bad.html'), '<h1>错误链接</h1><a href="/wrong/guide/start.html">错误</a>')
      assert.match(checkOutput(root, base).join('\n'), /链接离开帮助站/)
      rmSync(path.join(root, 'bad.html'))
    }
    const server = createHelpServer(root, base)
    await new Promise((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections() }))
    const origin = `http://127.0.0.1:${server.address().port}`
    const response = await fetch(`${origin}${base}guide/start.html`)
    assert.equal(response.status, 200)
    assert.match(await response.text(), /id="read"/)
    assert.match(response.headers.get('content-security-policy'), /default-src 'self'/)
    assert.equal((await fetch(`${origin}${base}guide/start.html`, { method: 'HEAD' })).status, 200)
    assert.equal((await fetch(`${origin}${base}missing.html`)).status, 404)
    assert.equal((await fetch(`${origin}${base}csp-hashes.json`)).status, 404)
    if (base !== '/') {
      const redirect = await fetch(`${origin}${base.slice(0, -1)}`, { redirect: 'manual' })
      assert.equal(redirect.status, 308)
      assert.equal(redirect.headers.get('location'), base)
      assert.equal((await fetch(`${origin}/wrong/index.html`)).status, 404)
    } else assert.equal((await fetch(`${origin}/`, { redirect: 'manual' })).status, 200)
    const range = await fetch(`${origin}${base}demo.mp4`, { headers: { Range: 'bytes=2-5' } })
    assert.equal(range.status, 206)
    assert.equal(await range.text(), '2345')
  })
}

test('built version, favicon and compatibility redirect use the selected output', () => {
  const html = readFileSync(path.join(site.outDir, 'index.html'), 'utf8')
  assert.ok(html.includes(`帮助站构建版本 ${buildVersion()}`))
  assert.ok(html.includes(`${repositoryUrl}/releases/tag/v${buildVersion()}`))
  assert.ok(html.includes(`${site.base}assets/design/favicon.svg`))
  const file = 'assets/previews/onboarding/index.html'
  const oldPage = readFileSync(path.join(site.outDir, file), 'utf8')
  const redirect = oldPage.match(/content="0; url=([^"]+)"/)?.[1]
  assert.equal(new URL(redirect, `https://docs.invalid${site.base}${file}`).pathname,
    `${site.base}user-guide/onboarding.html`)
})
