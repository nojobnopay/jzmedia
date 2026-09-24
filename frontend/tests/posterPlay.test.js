// 海报悬浮播放键回归网（2026-09 用户需求）：海报墙 + 继续观看中间的 ▶ 直接播放，
// 点卡片其余区域仍进详情（继续观看）/ 左上角选择（海报墙）。node --test 无 DOM 环境，
// 沿用 playerLifecycle.test.js 的 SFC 源码静态断言约定，防止后续重构把 stop/分支删掉。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src')
const library = readFileSync(path.join(SRC, 'views/Library.vue'), 'utf-8')
const season = readFileSync(path.join(SRC, 'views/SeasonView.vue'), 'utf-8')
const cw = readFileSync(path.join(SRC, 'components/ContinueWatchingRow.vue'), 'utf-8')
const app = readFileSync(path.join(SRC, 'App.vue'), 'utf-8')

test('海报墙播放键：stop 冒泡只播放，多选态隐藏', () => {
  assert.match(library, /class="poster-play"[\s\S]{0,300}@click\.stop="playMovie\(m\)"/,
    '播放键必须 @click.stop 调用 playMovie')
  assert.match(library, /v-if="!selecting"[\s\S]{0,300}class="poster-play"/,
    '多选态应隐藏播放键（点哪都切换选择）')
})

test('playMovie：单版本直接播，多版本走 /api/stream/versions 取 best_version_id 且失败回落', () => {
  const start = library.indexOf('async function playMovie(')
  assert.ok(start >= 0, 'Library.vue 缺少 playMovie')
  const body = library.slice(start, start + 1600)
  assert.match(body, /version_count/, '需按 version_count 决定是否智能选版')
  assert.match(body, /\/api\/stream\/versions/, '多版本需请求版本聚合')
  assert.match(body, /best_version_id/, '需取服务端最优版本')
  assert.match(body, /catch[\s\S]{0,120}选版失败回落/, '选版失败必须回落代表版本')
  assert.match(body, /playVid\.value = vid/, '最终用选定 vid 打开播放器')
})

test('海报墙卡片：其余区域进详情、左上角仍是选择', () => {
  assert.match(library, /@click="onCard\(m\)"/)
  assert.match(library, /function onCard\(m\)[\s\S]{0,200}router\.push\('\/m\/' \+ m\.id\)/)
  assert.match(library, /toggleSelect\(m\.id\)/)
})

test('继续观看：播放键 stop 续播，卡片点击进详情', () => {
  assert.match(cw, /class="poster-play"[\s\S]{0,200}@click\.stop="\$emit\('resume', m\)"/)
  assert.match(cw, /@click="\$emit\('open', m\.id\)"/)
})

test('季详情页集卡：中央播放键只播本集，卡片其余区域进详情（与电影墙一致）', () => {
  assert.match(season, /class="still-wrap"/, '季集卡容器为 still-wrap')
  assert.match(season, /v-if="e\.exists"[\s\S]{0,300}class="poster-play"/,
    '有片源才显示中央播放键')
  assert.match(season, /class="poster-play"[\s\S]{0,300}@click\.stop="play\(e\)"/,
    '播放键必须 stop 冒泡只播放，不进详情')
  assert.match(season, /@click="openEpisode\(e\.id\)"/, '卡片其余区域进集详情')
})

test('季集卡 hover 显形：still-wrap 与 poster-wrap 同规则（桌面端可见中央 ▶）', () => {
  assert.match(app, /\.still-wrap:hover \.poster-play/,
    '季集卡 hover 必须显形播放键，否则桌面端看不见')
})

test('共享 poster-play 样式：hover 海报显形 + hover 按钮放大变红（Plex 式）', () => {
  assert.match(app, /\.poster-wrap:hover \.poster-play/)
  assert.match(app, /\.poster-play:hover \{[\s\S]{0,220}scale\(1\.12\)/)
  assert.match(app, /\.poster-play:hover \{[\s\S]{0,220}background: #e50914/)
  assert.match(app, /@media \(hover: none\) \{ \.poster-play/, '触屏需常显播放键')
})
