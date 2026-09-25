<template>
  <div class="pd-setwrap">
      <button class="pd-mini" :class="{ on: open }" @click="$emit('toggle-settings')"
        title="播放设置（倍速/画质/音轨/字幕/预览）">⚙ 设置</button>
      <div v-if="open" class="pd-set" @click.stop>
        <div class="set-row">
          <label>速度</label>
          <select :value="playbackRate" @change="pick($event, 'rate-change', Number($event.target.value))">
            <option v-for="rate in PLAYBACK_RATES" :key="rate" :value="rate">{{ rate }}×</option>
          </select>
        </div>
        <div class="set-row">
          <label>进度预览</label>
          <button v-if="previewBusy" class="ctl-mini" @click="$emit('preview-cancel')">取消生成</button>
          <button v-else class="ctl-mini" @click="$emit('preview-start')">生成预览</button>
        </div>
        <p class="set-hint">{{ previewStatus }}。远程片源建议空闲时生成，也可在库工具中批量生成。</p>
        <div class="set-row">
          <label>画质</label>
          <select :value="quality" @change="pick($event, 'quality-change', $event.target.value)"
            title="自动=按服务器能力；原画=不封顶重编（耗 CPU）">
            <option value="auto">自动（推荐）</option>
            <option value="source">原画</option>
            <option value="1080p">1080p</option>
            <option value="720p">720p</option>
          </select>
        </div>
        <div class="set-row" v-if="audios.length > 1">
          <label>音轨</label>
          <select :value="audioIdx" @change="pick($event, 'audio-change', Number($event.target.value))">
            <option v-for="(a, i) in audios" :key="i" :value="i">{{ audioLabel(a, i) }}</option>
          </select>
        </div>
        <div class="set-row">
          <label>字幕</label>
          <select v-if="subs.length" :value="subIdx" @change="pick($event, 'sub-change', Number($event.target.value))">
            <option :value="-1">无字幕</option>
            <option v-for="(s, i) in subs" :key="i" :value="i">
              {{ subLabel(s, i) }}{{ subBadge(s) }}
            </option>
          </select>
          <span v-else class="fhint-inline">未检测到字幕轨（画面硬字幕无法关闭）</span>
          <button class="ctl-mini" @click="pickSubFile"
            title="临时加载本地字幕文件（srt/vtt/ass/ssa）：浏览器端解析，仅本次播放，不入库">加载文件</button>
          <button v-if="hasLocalSub" class="ctl-mini" @click="$emit('remove-local-subs')"
            title="移除临时加载的字幕">移除</button>
          <input ref="subFileInput" type="file" accept=".srt,.vtt,.ass,.ssa" class="sub-file" @change="onSubFile" />
        </div>
        <div class="set-row" v-if="subDelayVisible">
          <label>延迟</label>
          <span class="set-inline">
            <button class="ctl-mini" @click="$emit('shift-delay', -0.5)">−0.5</button>
            <span class="delay-val">{{ subDelayText }}</span>
            <button class="ctl-mini" @click="$emit('shift-delay', 0.5)">+0.5</button>
          </span>
        </div>
        <div class="set-row" v-if="subIsVtt">
          <label>外观</label>
          <span class="set-inline">
            <select :value="subStyle.bg" @change="styleSet('bg', Number($event.target.value))" title="字幕背景（只覆盖文字区域）">
              <option :value="0">无背景</option>
              <option :value="1">半透明底</option>
              <option :value="2">纯黑底</option>
            </select>
            <select :value="subStyle.outline" @change="styleSet('outline', Number($event.target.value))" title="字形描边（黑边，提升亮画面可读性）">
              <option :value="0">无描边</option>
              <option :value="1">细描边</option>
              <option :value="2">粗描边</option>
            </select>
          </span>
        </div>
        <div class="set-row" v-if="subIsVtt">
          <label>位置</label>
          <span class="set-inline">
            <select :value="subStyle.pos" @change="styleSet('pos', $event.target.value)"
              title="字幕位置：自动=下方黑边够高时落入黑边（不遮画面），否则画面内底部">
              <option value="auto">自动（黑边优先）</option>
              <option value="inside">画面内</option>
              <option value="outside">下黑边</option>
            </select>
            <select :value="subStyle.size" @change="styleSet('size', Number($event.target.value))"
              title="字号：随画面高度自适应缩放">
              <option :value="1">小</option>
              <option :value="2">中</option>
              <option :value="3">大</option>
            </select>
          </span>
        </div>
        <div class="set-row" v-if="subIsAss || subIsVtt || forceBurn">
          <span class="set-label">兼容降级</span>
          <span class="set-inline">
            <button class="ctl-mini" @click="$emit('undo-degrade')" :disabled="undoDisabled"
              title="取消 VTT 兼容/烧录降级，恢复 ASS/PGS 客户端渲染">恢复客户端渲染</button>
          </span>
        </div>
        <div class="set-row" v-if="subIsAss">
          <label>兼容</label>
          <label class="ctl-compat" title="ASS 渲染异常/缺字体时使用：改用简化 VTT 字幕">
            <input type="checkbox" :checked="compatSub"
              @change="$emit('compat-change', $event.target.checked)" />VTT 字幕（丢样式）
          </label>
        </div>
        <div class="set-row">
          <label>外部</label>
          <button class="ctl-mini" @click="$emit('copy-direct')"
            title="复制原文件直链：可用 VLC/Kodi/电视播放器打开（HDR/DV 等复杂片源推荐）">复制直链</button>
        </div>
        <input v-if="directFailUrl" readonly :value="directFailUrl" class="set-copy-url"
          @focus="$event.target.select()" @click="$event.target.select()" />
        <p class="set-hint">{{ methodLine }}<span v-if="qualityLine"> · {{ qualityLine }}</span></p>
      </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { audioLabel, subLabel, subBadge } from '../playerLabels.js'
import { PLAYBACK_RATES } from '../playbackControls.js'

const props = defineProps({
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
  'load-sub-file', 'remove-local-subs', 'rate-change', 'preview-start', 'preview-cancel'])

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
.pd-setwrap { position: relative; display: inline-flex; }
.pd-set { position: absolute; right: 0; top: calc(100% + 6px); z-index: 20; width: min(360px, 78vw);
  background: #1d1d1d; border: 1px solid #3a3a3a; border-radius: 10px; padding: 10px 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .5); display: flex; flex-direction: column; gap: 8px;
  max-height: min(72vh, 560px); overflow: auto; }
.set-row { display: flex; align-items: center; gap: 8px; }
.set-row > label, .set-label { color: #999; font-size: 0.75rem; width: 34px; flex: none; }
.set-row select { flex: 1; min-width: 0; background: #262626; color: #ddd; border: 1px solid #444; border-radius: 6px; padding: 4px 6px; font-size: 0.75rem; }
.set-inline { display: inline-flex; align-items: center; gap: 6px; }
.set-hint { margin: 2px 0 0; color: #777; font-size: 0.6875rem; overflow-wrap: anywhere; }
.set-copy-url { width: 100%; box-sizing: border-box; padding: 4px 6px; background: #141414;
  border: 1px dashed #444; border-radius: 6px; color: #bbb;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.6875rem;
  overflow-x: auto; white-space: nowrap; }
.ctl-compat { display: inline-flex; align-items: center; gap: 3px; color: #aaa; font-size: 0.75rem; white-space: nowrap; cursor: pointer; }
.ctl-compat input { margin: 0; }
.ctl-mini { padding: 1px 6px !important; font-size: 0.75rem; line-height: 1.2; }
.delay-val { min-width: 34px; text-align: center; color: #7ed321; }
.sub-file { display: none; }
.fhint-inline { flex: 1; min-width: 0; color: #777; font-size: 0.75rem; }
</style>
