import test from 'node:test'
import assert from 'node:assert/strict'
import { createSSRApp, ref } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createMemoryHistory, createRouter } from 'vue-router'
import { loadSfc } from './helpers/loadSfc.js'

const noRequest = () => { throw new Error('Component setup must not send requests') }
async function render(filename, props = {}, overrides = {}) {
  const component = await loadSfc(new URL('../src/' + filename, import.meta.url), {
    '../api.js': { api: noRequest, apiUpload: noRequest },
    ...overrides,
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }] })
  await router.push('/setup')
  return renderToString(createSSRApp(component, props).use(router))
}

test('TMDB form renders saved environment credentials without exposing a token field value', async () => {
  const html = await render('components/TmdbSettingsPanel.vue', { settings: {
    tmdb_configured: true, tmdb_read_token_source: 'env', tmdb_read_token_masked: '***1234',
    tmdb_language: 'zh-CN', tmdb_image_base: 'https://image.tmdb.org',
  } })
  assert.match(html, /已配置凭据 · 服务器环境配置/)
  assert.match(html, /保存并测试 TMDB 连接/)
  assert.match(html, /留空保留现有配置/)
  assert.doesNotMatch(html, /value="\*\*\*1234"/)
})
test('empty video library editor and setup library step initialize with their actual shared forms', async () => {
  const editor = await render('components/VideoLibraryForm.vue', { library: { id: 1, kind: 'movie' } })
  assert.match(editor, /空库可调整类型/)
  const step = await render('components/SetupLibraryStep.vue', { accept: async () => {} })
  assert.match(step, /这次添加什么内容/)
  assert.match(step, /使用已有视频库/)
})

for (const step of [1, 2, 3, 4]) {
  test('guide step ' + step + ' renders without setup errors or side effects', async () => {
    const state = {
      step, status: 'active', kind: 'movie', import_mode: 'scan', library_id: 1,
      upload_allowed: false, can_complete: step === 4, content: {
        count: step === 4 ? 1 : 0, pending: step === 4 ? 1 : 0,
        items: step === 4 ? [{ id: 9, title: '测试影片', pending: true }] : [],
      },
    }
    const html = await render('views/Setup.vue', {}, {
      '../useOnboarding.js': { useOnboarding: () => ({
        state: ref(state), busy: ref(false), error: ref(''), refresh: noRequest, save: noRequest,
      }) },
    })
    assert.match(html, /让第一部内容进入媒体库/)
    if (step === 3) {
      assert.match(html, /还没有可展示的内容/)
      assert.match(html, /当前未确认写入权限/)
    }
    if (step === 4) assert.match(html, /href="\/m\/9"/)
  })
}
