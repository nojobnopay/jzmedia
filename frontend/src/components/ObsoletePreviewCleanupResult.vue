<template>
  <div class="cleanup-confirmation" aria-label="废弃缩略图清理检查结果" role="region">
    <template v-if="summary">
      <p>可清理 {{ candidate.candidates }} 个目录，释放约 {{ fmtBytes(candidate.candidate_bytes) }}。</p>
      <p v-if="!candidate.candidates" class="hint">本次检查没有可安全清理的废弃缩略图。</p>
      <p>废弃原因：<span v-for="(item, index) in reasons" :key="item.key">{{ index ? ' · ' : '' }}{{ item.label }} {{ candidate.reasons?.[item.key] ?? 0 }}</span></p>
    </template>
    <p>跳过：<span v-for="(item, index) in skips" :key="item.key">{{ index ? ' · ' : '' }}{{ item.label }} {{ candidate.skipped?.[item.key] ?? 0 }}</span></p>
    <ul v-if="details.length" class="cleanup-skip-details" aria-label="暂时保留的原因">
      <li v-for="item in details" :key="item.key"><strong>{{ item.label }} {{ item.count }} 项：</strong>{{ item.hint }}</li>
    </ul>
    <p v-if="summary" class="hint">确认后会重新检查状态，只删除仍确认废弃的目录。</p>
    <div v-if="actions" class="bar">
      <JzButton v-if="candidate.candidates" type="button" variant="danger" :disabled="disabled" :loading="busy" @click="emit('confirm')">确认清理废弃缩略图</JzButton>
      <JzButton type="button" :disabled="busy || disabled" @click="emit('cancel')">取消</JzButton>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtBytes } from '../format.js'
import JzButton from './JzButton.vue'

const props = defineProps({
  candidate: { type: Object, required: true }, summary: { type: Boolean, default: true },
  actions: { type: Boolean, default: true }, disabled: Boolean, busy: Boolean,
})
const emit = defineEmits(['confirm', 'cancel'])
const reasons = [
  { key: 'deleted', label: '媒体已删除' }, { key: 'replaced', label: '旧片源' },
  { key: 'superseded', label: '旧画质或抽帧规则' }, { key: 'invalid', label: '无法归属或损坏' },
]
const skips = [
  { key: 'in_use', label: '使用或排队' }, { key: 'offline', label: '离线或扫描未完成' },
  { key: 'protected', label: '最近访问或写入' }, { key: 'unknown', label: '状态无法确认' },
]
const detailDefinitions = [
  { key: 'replacement_missing', label: '尚无完整替代预览', hint: '先完成资料核对和目录整理，再生成当前片源的缩略图；成品完整后重新检查。' },
  { key: 'scan_required', label: '扫描证据不足', hint: '先完成相关视频库的完整扫描；离线、扫描未完成或有待扫描变更时保留缓存。' },
  { key: 'recently_used', label: '近期访问或写入', hint: '最近 24 小时访问或写入的缓存暂时保留，可稍后重新检查。' },
  { key: 'in_use', label: '使用或排队', hint: '正在使用、排队或已提供给播放器的缓存会保留，可稍后重新检查。' },
  { key: 'source_unavailable', label: '片源状态无法确认', hint: '检查媒体库连接和文件状态，恢复后重新检查。' },
  { key: 'unknown', label: '其他状态无法确认', hint: '暂时无法确认清理是否安全，先保留并稍后重新检查。' },
]
const details = computed(() => detailDefinitions.map(item => ({
  ...item, count: Math.max(0, Math.trunc(Number(props.candidate.skipped_details?.[item.key]) || 0)),
})).filter(item => item.count > 0))
</script>

<style scoped>
.cleanup-confirmation { margin-top: 12px; padding: 12px; border: 1px solid var(--jz-warn-border); border-radius: var(--jz-radius-s); }
.cleanup-confirmation p { margin: 8px 0 0; line-height: 1.6; overflow-wrap: anywhere; }
.cleanup-confirmation p:first-child { margin-top: 0; }
.cleanup-confirmation .bar { padding: 12px 0 0; flex-wrap: wrap; }
.cleanup-skip-details { margin: 12px 0 0; padding-left: 20px; line-height: 1.6; }
.cleanup-skip-details li + li { margin-top: 8px; }
.cleanup-skip-details strong { font-weight: 500; }
</style>
