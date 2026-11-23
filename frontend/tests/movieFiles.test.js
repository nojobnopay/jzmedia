// 文件与版本行对齐回归：大小列 + 三个动作槽全部定宽，版本行与其它文件行逐列对齐。
// 布局约束读取样式，删除流程通过 Vue 渲染器操作实际控件。
import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive } from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const COMPONENT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), '../src/components/MovieFileManager.vue')
const src = readFileSync(COMPONENT, 'utf-8')
const template = src.slice(0, src.indexOf('<script'))
const style = src.slice(src.indexOf('<style'))

test('版本行无侧边徽标，状态由播放按钮表达', () => {
  assert.doesNotMatch(template, /friendly-chip/, '绿勾徽标必须移除')
  assert.doesNotMatch(template, /trans-chip/, '转码徽标必须移除')
  assert.match(template, /transcode-play/, '转码状态必须由播放按钮表达')
})

test('版本行动顺序为下载-播放-更多', () => {
  const download = template.indexOf('下载</a><JzButton v-if="verBlocked')
  assert.ok(download >= 0, '版本行必须有下载链接')
  const play = template.indexOf('emit(\'play\', v)', download)
  assert.ok(play > download, '播放必须在下载之后')
  const more = template.indexOf('<ActionMenu label="更多">', play)
  assert.ok(more > play, '更多必须在播放之后')
})

test('大小列与三个动作槽定宽', () => {
  assert.match(style, /\.f-size\s*\{[^}]*flex:\s*0\s*0\s*4rem/, '大小列必须定宽')
  assert.match(style, /\.f-acts\s*>\s*a\s*\{[^}]*flex:\s*0\s*0\s*3\.6em/, '下载槽必须定宽')
  assert.match(style, /\.f-acts\s*\.jz-button:not\(\.danger\)\s*\{[^}]*flex:\s*0\s*0\s*6\.4em/, '按钮槽必须定宽')
  assert.match(style, /:deep\(\.action-menu summary\)\s*\{[^}]*flex:\s*0\s*0\s*6\.4em/, '更多触发器必须与删除同宽')
})

test('文件分组 ul 无浏览器默认缩进', () => {
  assert.match(style, /\.files ul\s*\{[^}]*padding:\s*0/, '分组 ul 必须清掉默认左缩进')
})

test('movie details confirm the returned relative path and discard stale or late previews', async t => {
  const gate = deferred(), requests = []
  let delayed = false
  const component = await loadSfc(new URL('../src/components/MovieFileManager.vue', import.meta.url), {
    '../api.js': { api: async (path, options) => {
      const body = JSON.parse(options.body)
      requests.push({ path, body })
      if (!body.dry_run) throw Object.assign(new Error('文件已变化'), { status: 409 })
      if (delayed) await gate.promise
      return { plans: [{ rel: '电影/Film.srt' }], preview_id: 'subtitle-version' }
    } },
    './FilePreviewHost.vue': { default: { render: () => null } },
  })
  const props = reactive({ movieId: 12, movie: { versions: [] }, sideFiles: { subtitles: [{ name: 'Film.srt' }] } })
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  const button = label => ui.find(node => node.type === 'button' && nodeText(node) === label)
  await button('删除').props.onClick()
  await flush()
  await button('确认删除').props.onClick()
  await flush()
  assert.deepEqual(requests.at(-1), { path: '/api/movies/12/files',
    body: { name: '电影/Film.srt', preview_id: 'subtitle-version', dry_run: false, confirm: true } })
  assert.ok(button('删除'))
  assert.equal(button('确认删除'), undefined)
  delayed = true
  const old = button('删除').props.onClick()
  props.movieId = 22
  await flush()
  gate.resolve()
  await old
  await flush()
  assert.equal(button('确认删除'), undefined)
})
