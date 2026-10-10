<template>
  <div class="pd-setwrap">
    <button type="button" class="player-icon-btn settings-trigger" :class="{ on: open }" @click="$emit('toggle-settings')"
      aria-label="播放设置" :aria-expanded="open" title="播放设置"><PlayerIcon name="settings" /></button>
    <section v-if="open" class="pd-set" :style="{ maxHeight: panelHeight + 'px' }" aria-label="播放设置" @click.stop>
      <header class="set-header">
        <strong>播放设置</strong>
        <HelpLink page="user-guide/playback-controls" label="帮助" />
        <button type="button" class="player-icon-btn" @click="$emit('toggle-settings')" aria-label="关闭设置" title="关闭设置"><PlayerIcon name="close" :size="18" /></button>
      </header>
      <div class="set-section">
        <div class="section-label">播放速度</div>
        <div class="rate-options" role="group" aria-label="播放速度">
          <button type="button" v-for="rate in PLAYBACK_RATES" :key="rate" :class="{ selected: playbackRate === rate }"
            :aria-pressed="playbackRate === rate" @click="pick($event, 'rate-change', rate)">{{ rate }}×</button>
        </div>
        <div class="set-row">
          <label for="player-quality">画质</label>
          <select id="player-quality" :value="quality" @change="pick($event, 'quality-change', $event.target.value)">
            <option value="auto">自动（推荐）</option>
            <option value="source">原画</option>
            <option value="1080p">1080p</option>
            <option value="720p">720p</option>
          </select>
        </div>
        <div v-if="audios.length > 1" class="set-row">
          <label for="player-audio">音轨</label>
          <select id="player-audio" :value="audioIdx" @change="pick($event, 'audio-change', Number($event.target.value))">
            <option v-for="(a, i) in audios" :key="i" :value="i">{{ audioLabel(a, i) }}</option>
          </select>
        </div>
      </div>
      <div class="set-section">
        <div class="section-label">字幕</div>
        <select v-if="subs.length" class="sub-select" aria-label="字幕轨道" :value="subIdx" @change="pick($event, 'sub-change', Number($event.target.value))">
          <option :value="-1">关闭字幕</option>
          <option v-for="(s, i) in subs" :key="i" :value="i">{{ subLabel(s, i) }}{{ subBadge(s) }}</option>
        </select>
        <p v-else class="set-hint">暂无字幕，可加载本地字幕文件。</p>
        <div class="sub-file-actions">
          <HelpLink page="user-guide/subtitles" label="音轨与字幕图解" />
          <JzButton size="compact" class="set-button" @click="pickSubFile" title="临时加载 SRT、VTT、ASS 或 SSA 字幕，仅本次播放有效"><PlayerIcon name="subtitles" :size="16" />加载字幕文件</JzButton>
          <JzButton size="compact" v-if="hasLocalSub" class="set-button" @click="$emit('remove-local-subs')">移除本地字幕</JzButton>
          <input ref="subFileInput" type="file" accept=".srt,.vtt,.ass,.ssa" class="sub-file" @change="onSubFile" />
        </div>
        <details v-if="subDelayVisible || subIsVtt || subIsAss || forceBurn" class="set-details">
          <summary>字幕调整<PlayerIcon name="chevron" :size="14" /></summary>
          <div v-if="subDelayVisible" class="set-row">
            <span class="set-label">时间偏移</span>
            <span class="set-inline">
              <JzButton size="compact" class="set-button" @click="$emit('shift-delay', -0.5)" aria-label="字幕提前 0.5 秒">−0.5s</JzButton>
              <span class="delay-val">{{ subDelayText }}</span>
              <JzButton size="compact" class="set-button" @click="$emit('shift-delay', 0.5)" aria-label="字幕延后 0.5 秒">+0.5s</JzButton>
            </span>
          </div>
          <div v-if="subIsVtt" class="set-row">
            <span class="set-label">外观</span>
            <span class="set-inline">
              <select :value="subStyle.bg" @change="styleSet('bg', Number($event.target.value))" aria-label="字幕背景">
                <option :value="0">无背景</option><option :value="1">半透明底</option><option :value="2">纯黑底</option>
              </select>
              <select :value="subStyle.outline" @change="styleSet('outline', Number($event.target.value))" aria-label="字幕描边">
                <option :value="0">无描边</option><option :value="1">细描边</option><option :value="2">粗描边</option>
              </select>
            </span>
          </div>
          <div v-if="subIsVtt" class="set-row">
            <span class="set-label">位置 / 大小</span>
            <span class="set-inline">
              <select :value="subStyle.pos" @change="styleSet('pos', $event.target.value)" aria-label="字幕位置">
                <option value="auto">自动</option><option value="inside">画面内</option><option value="outside">下黑边</option>
              </select>
              <select :value="subStyle.size" @change="styleSet('size', Number($event.target.value))" aria-label="字幕字号">
                <option :value="1">小</option><option :value="2">中</option><option :value="3">大</option>
              </select>
            </span>
          </div>
          <label v-if="subIsAss" class="ctl-compat">
            <input type="checkbox" :checked="compatSub" @change="$emit('compat-change', $event.target.checked)" />兼容模式（使用简化字幕）
          </label>
          <JzButton size="compact" v-if="subIsAss || subIsVtt || forceBurn" class="set-button" @click="$emit('undo-degrade')" :disabled="undoDisabled">恢复客户端渲染</JzButton>
        </details>
      </div>
      <details class="set-section set-details more-settings">
        <summary>更多选项<PlayerIcon name="chevron" :size="14" /></summary>
        <div class="set-row">
          <span class="set-label">进度缩略图</span>
          <JzButton size="compact" v-if="previewBusy" class="set-button" @click="$emit('preview-cancel')">取消生成</JzButton>
          <JzButton size="compact" v-else class="set-button" @click="$emit('preview-start')">生成预览</JzButton>
        </div>
        <p class="set-hint">{{ previewStatus }}。远程片源建议空闲时生成。</p>
        <JzButton size="compact" class="set-button external-button" @click="$emit('copy-direct')" title="复制原文件直链，在 VLC、Kodi 等播放器打开"><PlayerIcon name="external" :size="16" />复制播放直链</JzButton>
        <input v-if="directFailUrl" readonly :value="directFailUrl" class="set-copy-url" aria-label="播放直链"
          @focus="$event.target.select()" @click="$event.target.select()" />
        <div class="play-info">
          <span class="section-label">播放信息</span>
          <p>{{ methodLine }}</p><p v-if="qualityLine">{{ qualityLine }}</p>
          <p v-if="reasonLine">{{ reasonLine }}</p><p v-if="bufferLine">{{ bufferLine }}</p>
          <JzButton size="compact" class="set-button" @click="$emit('copy-debug')">复制诊断信息</JzButton>
        </div>
      </details>
    </section>
  </div>
</template>

<script setup>
import HelpLink from './HelpLink.vue'
import { ref } from 'vue'
import PlayerIcon from './PlayerIcon.vue'
import JzButton from './JzButton.vue'
import { audioLabel, subLabel, subBadge } from '../playerLabels.js'
import { PLAYBACK_RATES } from '../playbackControls.js'

const props = defineProps({
  panelHeight: { type: Number, default: 400 },
  reasonLine: { type: String, default: '' },
  bufferLine: { type: String, default: '' },
  playbackRate: { type: Number, default: 1 },
  previewBusy: Boolean,
  previewStatus: { type: String, default: '' },
  open: { type: Boolean, default: false },
  quality: { type: String, default: 'auto' },
  audios: { type: Array, default: () => [] },
  audioIdx: { type: Number, default: 0 },
  subs: { type: Array, default: () => [] },
  subIdx: { type: Number, default: -1 },
  subDelayVisible: { type: Boolean, default: false },
  subDelayText: { type: String, default: '' },
  subIsVtt: { type: Boolean, default: false },
  subIsAss: { type: Boolean, default: false },
  forceBurn: { type: Boolean, default: false },
  undoDisabled: { type: Boolean, default: false },
  compatSub: { type: Boolean, default: false },
  hasLocalSub: { type: Boolean, default: false },
  directFailUrl: { type: String, default: '' },
  methodLine: { type: String, default: '' },
  qualityLine: { type: String, default: '' },
  subStyle: { type: Object, default: () => ({ bg: 0, outline: 0, pos: 'auto', size: 2 }) }
})
const emit = defineEmits(['toggle-settings', 'quality-change', 'audio-change', 'sub-change',
  'shift-delay', 'update:sub-style', 'undo-degrade', 'compat-change', 'copy-direct',
  'load-sub-file', 'remove-local-subs', 'rate-change', 'preview-start', 'preview-cancel', 'copy-debug'])

const subFileInput = ref(null)
function pickSubFile() {
  const el = subFileInput.value
  if (el) el.click()
}
function onSubFile(e) {
  const f = e && e.target && e.target.files && e.target.files[0]
  if (f) emit('load-sub-file', f)
  try { if (e && e.target) e.target.value = '' } catch (err) { /* 忽略 */ }   // 允许重复选同一文件
}

// 选完即失焦：否则焦点停在下拉框，方向键会去改选项而不是 seek/音量
function pick(e, name, value) {
  emit(name, value)
  try {
    const el = e && e.target
    if (el && el.blur) el.blur()
  } catch (err) { /* 忽略 */ }
}
function styleSet(key, value) {
  emit('update:sub-style', { ...props.subStyle, [key]: value })
}
</script>

<style scoped>
.pd-setwrap { display: inline-flex; position: static; }
.settings-trigger.on { background: rgba(255,255,255,.12); color: var(--jz-on-accent); }
.pd-set { position: absolute; right: 16px; bottom: calc(100% + 8px); z-index: 20;
  width: min(350px, calc(100% - 32px)); box-sizing: border-box; overflow: auto;
  color: var(--jz-text); background: var(--jz-surface); border: 1px solid rgba(255,255,255,.12);
  border-radius: var(--jz-radius-dialog); box-shadow: 0 12px 40px #0008; scrollbar-width: thin; scrollbar-color: #555 transparent;
  text-align: left; font-size: .8125rem; color-scheme: dark; }
.set-header { position: sticky; top: 0; z-index: 1; background: var(--jz-surface); display: flex; justify-content: space-between; align-items: center; padding: 8px 12px 8px 18px;
  border-bottom: 1px solid #ffffff10; }
.set-header strong { font-size: .875rem; font-weight: 600; }
.set-section { padding: 14px 18px; border-bottom: 1px solid #ffffff10; }
.set-section:last-child { border-bottom: 0; }
.section-label { display: block; color: var(--jz-text-dim); font-size: .6875rem; letter-spacing: .06em; margin-bottom: 10px; }
.rate-options { display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px; margin-bottom: 14px; }
.rate-options button { min-height: var(--jz-control-current); border: 0; padding: 8px 0; font-size: .75rem; font-weight: 500; background: #ffffff09; color: var(--jz-text-dim); border-radius: var(--jz-radius-s); }
.rate-options button:hover { color: var(--jz-on-accent); background: #ffffff18; }
.rate-options button.selected { background: var(--player-accent); color: var(--jz-on-accent); }
.set-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; min-width: 0; }
.set-row > label, .set-label { flex: 1; color: var(--jz-text-dim); font-size: .75rem; white-space: nowrap; }
.pd-set select { min-width: 0; box-sizing: border-box; border: 1px solid #ffffff12; background: #ffffff08;
  color: var(--jz-text); font-size: .75rem; padding: 7px 12px; border-radius: var(--jz-radius-s); max-width: 70%; }
.pd-set select option { background: var(--jz-surface-2); color: var(--jz-text); }
.pd-set select.sub-select { width: 100%; max-width: 100%; }
.set-inline { display: flex; align-items: center; justify-content: flex-end; gap: 5px; min-width: 0; }
.set-inline select { flex: 1; }
.set-button { gap: var(--jz-gap-xs); }
.sub-file-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.set-hint { margin: 7px 0 0; color: var(--jz-text-dim); font-size: .6875rem; line-height: 1.7; overflow-wrap: anywhere; }
.set-details summary { display: flex; align-items: center; justify-content: space-between; list-style: none;
  color: var(--jz-text-dim); padding: 12px 0 0; font-size: .75rem; cursor: pointer; }
.set-details summary::-webkit-details-marker { display: none; }
.set-details[open] > summary { margin-bottom: 10px; }
.set-details[open] > summary svg { transform: rotate(90deg); }
.more-settings > summary { padding: 0; }
.ctl-compat { display: flex; align-items: center; gap: 8px; margin: 12px 0; font-size: .75rem; cursor: pointer; }
.ctl-compat input { accent-color: var(--player-accent); }
.delay-val { min-width: 38px; text-align: center; font-variant-numeric: tabular-nums; color: var(--jz-on-accent); font-size: .75rem; }
.sub-file { display: none; }
.external-button { margin-top: 14px; }
.set-copy-url { width: 100%; box-sizing: border-box; margin-top: 8px; font-size: .6875rem; }
.play-info { margin-top: 14px; padding-top: 14px; border-top: 1px solid #ffffff10; }
.play-info p { margin: 6px 0; color: var(--jz-text-dim); font-size: .6875rem; line-height: 1.6; overflow-wrap: anywhere; }
.play-info .set-button { margin-top: 6px; }
@media (max-width: 700px), (pointer: coarse) {
  .rate-options { grid-template-columns: repeat(3, 1fr); }
  .pd-set select, .set-details summary { min-height: var(--jz-touch-target); }
  .set-row { flex-wrap: wrap; }
  .set-inline { flex-wrap: wrap; }
}
</style>
