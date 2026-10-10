// 文件与版本行对齐回归：大小列 + 三个动作槽全部定宽，版本行与其它文件行逐列对齐。
// 无 DOM 环境，按本仓既有约定做 SFC 源码静态断言。
import test from 'node:test'
import assert from 'node:assert/strict'
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
