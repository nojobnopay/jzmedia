<template>
  <JzDialog class="tv-ingest-dialog" size="large" :busy="busy" :title="heading" close-label="稍后处理剧集整理" @close="close">
    <template v-if="running">
      <div class="ingest-task" role="status" aria-live="polite"><Spinner :size="28" /><h3>正在整理选中的剧集</h3><p>目录和文件正在更新，请等待任务完成。</p>
        <progress :value="total ? done : undefined" :max="total || 1" aria-label="剧集整理进度"></progress><p v-if="total">已处理 {{ done }} / {{ total }}</p></div>
      <p v-if="pollError" class="feedback" role="status">{{ pollError }}</p>
    </template>
    <template v-else-if="phase === 'done'">
      <div class="ingest-task" role="status"><AppIcon :name="result.failed ? 'warning' : 'check'" :size="28" /><h3>{{ result.failed ? '整理完成，部分项目未成功' : '整理已完成' }}</h3>
        <p>移动或改名 {{ Number(result.moved || 0) + Number(result.renamed || 0) }} 项<span v-if="result.skipped"> · 跳过 {{ result.skipped }} 项</span><span v-if="result.failed"> · 失败 {{ result.failed }} 项</span></p><p>可在整理历史中查看或撤销。未成功的项目可重新检查。</p></div>
    </template>
    <template v-else>
      <p class="ingest-intro">已检查 {{ plans.length }} 部剧的资料、季集号与文件命名。资料补全结果如下；确认后执行选中的目录与文件更改。</p>
      <p class="ingest-scope">已补全 {{ filled }} 集资料 · 选中 {{ selected.length }} 部剧<span v-if="loading"> · 正在更新预览…</span></p>
      <p v-if="!plans.length" class="feedback" role="status">资料已检查，当前没有需要整理的项目。</p>
      <article v-for="plan in plans" :key="plan.show_id" class="ingest-show" :aria-busy="loading">
        <div class="ingest-show-heading"><label><input type="checkbox" v-model="selected" :value="Number(plan.show_id)" :disabled="busy || !plan.groups?.length || plan.blocked || plan.reason === 'read_only'" /><strong>{{ plan.title }}</strong></label>
          <span v-if="plan.blocked" class="ingest-warning">做种保护，保持原样</span><span v-else-if="plan.reason === 'read_only'" class="ingest-warning">只读库，保持原样</span><span v-else>{{ plan.groups?.length || 0 }} 项整理</span></div>
        <p class="ingest-metadata">{{ metadataText(plan.metadata) }}</p>
        <ul v-if="plan.metadata?.issues?.length" class="ingest-issues"><li v-for="(issue, index) in plan.metadata.issues" :key="index">{{ issue.message || issue.code || issue }}</li></ul>
        <details :open="plans.length === 1" class="ingest-changes"><summary>查看目录、文件名与风险</summary>
          <div v-for="(group, index) in plan.groups || []" :key="index" class="ingest-group"><h3>{{ groupText(group) }}</h3>
            <div v-if="group.dir" class="ingest-path-pair"><span>原路径：{{ group.from }}/</span><span>新路径：{{ group.to }}/</span></div>
            <div v-for="(sample, sampleIndex) in group.samples || []" :key="sampleIndex" class="ingest-path-pair"><span>{{ sample.from }}</span><span><AppIcon name="arrow-right" :size="16" /> {{ sample.to }}</span></div></div>
          <p v-if="plan.dir_totals?.length" class="ingest-destination">整理后的正片位置：{{ dirTotalsText(plan) }}</p><TvOrganizeNotes :plan="plan" /></details>
        <label v-if="plan.absolute_risk && actions.includes('rename')" class="ingest-risk"><input type="checkbox" v-model="allowAbsolute" :value="Number(plan.show_id)" :disabled="busy" /><span>我已核对「{{ plan.title }}」集号，允许按 TMDB 编号改名</span></label>
      </article>
      <details class="ingest-options"><summary>调整整理范围</summary><div class="ingest-options-grid"><label v-for="(help, action) in ACTION_HELP" :key="action"><input type="checkbox" v-model="actions" :value="action" :disabled="busy" /><span><b>{{ help.label }}</b><span>{{ help.desc }}</span></span></label></div>
        <label class="ingest-risk"><input type="checkbox" v-model="allowTorrent" :disabled="busy" /><span>允许整理含 .torrent 的剧集目录（可能影响做种）</span></label><p>修改范围或风险选项后会重新预览；字幕与 NFO 随正片移动。</p></details>
      <p v-if="error" class="feedback error" role="alert">{{ error }}</p><p v-if="!actions.length" class="feedback">请选择至少一个整理项目。</p>
    </template>
    <template #footer>
      <JzButton class="ingest-history" variant="ghost" :disabled="busy" icon="history" @click="history">整理历史</JzButton>
      <template v-if="phase === 'done'"><JzButton v-if="result.failed" :disabled="busy" icon="refresh" @click="refresh()">重新检查</JzButton><JzButton variant="primary" @click="close">完成</JzButton></template>
      <template v-else-if="running"><JzButton variant="primary" disabled>正在整理…</JzButton></template>
      <template v-else><JzButton v-if="decisionFailed" :disabled="busy" @click="closePending">关闭，保留待办</JzButton><JzButton v-if="error || phase === 'failed'" :disabled="busy" icon="eye" @click="refresh()">重新预览</JzButton><JzButton v-if="hasMetadataIssues" :disabled="busy" icon="refresh" @click="refresh(true)">重试资料补全</JzButton>
        <JzButton :disabled="busy" @click="decide('ignored')">忽略本次</JzButton><JzButton :disabled="busy" @click="decide('deferred')">稍后处理</JzButton><JzButton variant="primary" :loading="loading" :disabled="!canConfirm" icon="organize" @click="confirm">确认并整理</JzButton></template>
    </template>
  </JzDialog>
</template>
<script setup>
import { computed } from 'vue'
import JzDialog from './JzDialog.vue'
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'
import Spinner from './Spinner.vue'
import TvOrganizeNotes from './TvOrganizeNotes.vue'
import { ACTION_HELP, dirTotalsText, groupText } from '../tvOrganizePlans.js'
import { useTvIngestReview } from '../useTvIngestReview.js'
import { hasMetadataIssues as metadataNeedsRetry, ingestionMetadataText } from '../tvIngestFeedback.js'
const props = defineProps({ initialSummary: { type: Object, required: true } })
const emit = defineEmits(['settings'])
const { plans, selected, actions, allowTorrent, allowAbsolute, phase, loading, busy, running, error,
  pollError, done, total, result, decisionFailed, canConfirm, refresh, confirm, decide, close, closePending } = useTvIngestReview(props.initialSummary)
const heading = computed(() => ({ preview: '剧集入库与整理预览', running: '正在整理剧集', done: '剧集整理结果', failed: '剧集整理未完成' }[phase.value]))
const filled = computed(() => plans.value.reduce((sum, plan) => sum + (Number(plan.metadata?.filled) || 0), 0))
const hasMetadataIssues = computed(() => plans.value.some(plan => metadataNeedsRetry(plan.metadata)))
const metadataText = ingestionMetadataText
async function history() {
  const library = plans.value[0]?.library_id
  if (phase.value === 'done') close()
  else await decide('deferred')
  if (!error.value) emit('settings', library)
}
</script>
<style scoped>
.ingest-intro { margin: 0 0 10px; color: var(--jz-text-dim); }
.ingest-scope { margin: 0 0 20px; color: var(--jz-text); }
.ingest-show { margin: 12px 0; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); overflow: hidden; }
.ingest-show-heading { display: flex; flex-wrap: wrap; gap: 8px 14px; justify-content: space-between; align-items: center; padding: 10px 16px; background: var(--jz-surface-2); }
.ingest-show-heading label { display: flex; align-items: center; gap: 10px; min-height: 36px; min-width: 0; overflow-wrap: anywhere; }
.ingest-show-heading > span { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.ingest-metadata { margin: 12px 16px 6px; color: var(--jz-text-dim); }
.ingest-issues { margin: 8px 16px; padding-left: 20px; color: var(--jz-warn); }
.ingest-changes { margin: 0 16px 12px; }
summary { cursor: pointer; min-height: 36px; line-height: 36px; }
.ingest-group { margin: 10px 0; padding: 12px; background: var(--jz-bg); border-radius: var(--jz-radius-m); }
.ingest-group h3 { margin: 0 0 8px; font-size: var(--jz-font-m); font-weight: 500; }
.ingest-path-pair { display: grid; gap: 4px; padding: 8px 0; line-height: 1.5; overflow-wrap: anywhere; }
.ingest-path-pair > span:first-child { color: var(--jz-text-dim); }
.ingest-path-pair svg { vertical-align: middle; }
.ingest-destination { color: var(--jz-text-dim); }
.ingest-options { padding: 10px 16px; margin-top: 18px; border-top: 1px solid var(--jz-border); }
.ingest-options-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px 20px; margin-top: 10px; }
.ingest-options-grid label, .ingest-risk { display: flex; gap: 10px; align-items: flex-start; }
.ingest-options-grid label > span { display: grid; gap: 3px; }
.ingest-options-grid label span span, .ingest-options p { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.ingest-risk { padding: 12px 16px; color: var(--jz-warn); }
.ingest-history { margin-right: auto; }
.ingest-task { display: grid; justify-items: center; gap: 14px; padding: 24px 0; text-align: center; }
.ingest-task h3, .ingest-task p { margin: 0; }
.ingest-task progress { width: min(420px, 100%); }
.error { color: var(--jz-danger); }
@media (max-width: 700px) {
  .ingest-show-heading label, summary, .ingest-options-grid label, .ingest-risk { min-height: 44px; }
  .ingest-options-grid { grid-template-columns: 1fr; }
  .ingest-history { margin-right: 0; }
}
</style>
