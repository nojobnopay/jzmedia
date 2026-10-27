import test from 'node:test'
import assert from 'node:assert/strict'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { loadSfc } from './helpers/loadSfc.js'

const quick = () => loadSfc(new URL('../src/components/PlayerQuickControls.vue', import.meta.url))

test('existing local track controls retain subtitle-file and compatibility actions by default', async () => {
  const html = await renderToString(createSSRApp(await quick(), { panel: 'tracks' }))
  assert.match(html, /加载字幕文件/)
  assert.match(html, /字幕调整与兼容设置/)
  assert.match(html, /accept="\.srt,\.vtt,\.ass,\.ssa"/)
})

test('online playback exposes only real track choices while retaining speed controls', async () => {
  const html = await renderToString(createSSRApp(await quick(), {
    panel: 'tracks', allowLocalSubtitles: false, allowSubtitleSettings: false,
    audios: [{ lang: 'zh', title: '国语' }, { lang: 'en', title: '英语' }],
    subs: [{ lang: 'zh', codec: 'vtt' }],
  }))
  assert.match(html, /国语/)
  assert.match(html, /英语/)
  assert.match(html, /字幕轨道/)
  assert.match(html, /播放速度 1 倍/)
  assert.doesNotMatch(html, /加载字幕文件|字幕调整与兼容设置|type="file"/)
})

test('a source with no selectable tracks keeps speed but hides an empty track menu', async () => {
  const html = await renderToString(createSSRApp(await quick(), {
    showTracks: false, panel: 'tracks', allowLocalSubtitles: false, allowSubtitleSettings: false,
  }))
  assert.match(html, /播放速度 1 倍/)
  assert.doesNotMatch(html, /aria-label="字幕与音轨"|加载字幕文件|字幕调整与兼容设置/)
})

test('seekbar hint override is opt-in so existing local callers keep their default behavior', async () => {
  const component = await loadSfc(new URL('../src/components/PlayerSeekbar.vue', import.meta.url))
  assert.equal(component.props.previewHintText.default, null)
  assert.equal(component.props.manifest.default, null)
})
