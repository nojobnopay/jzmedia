<template>
  <Teleport to="body">
    <div class="organize-mask" @click.self="close" @keydown.esc.stop.prevent="close">
      <section ref="dialog" class="organize-dialog" role="dialog" aria-modal="true"
        aria-labelledby="organize-title" aria-describedby="organize-subtitle">
        <header class="dialog-header">
          <div class="header-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M3 7V5h6l2 2h10v12H3z" /></svg></div>
          <div class="header-copy">
            <h2 id="organize-title">{{ heading }}</h2>
            <p id="organize-subtitle">{{ title }}<span v-if="year">（{{ year }}）</span></p>
          </div>
          <button class="close-button" aria-label="关闭整理对话框" :disabled="running" @click="close">×</button>
        </header>

        <div ref="body" class="dialog-body">
          <template v-if="phase === 'running'">
            <div class="task-state" role="status" aria-live="polite">
              <Spinner :size="28" />
              <h3>正在整理本剧</h3>
              <p>文件和目录正在更新，请等待任务完成。</p>
              <progress :value="total ? done : undefined" :max="total || 1" aria-label="整理进度"></progress>
              <p v-if="total" class="task-count">已处理 {{ done }} / {{ total }}</p>
            </div>
            <p v-if="pollError" class="feedback" role="status">{{ pollError }}</p>
          </template>
          <template v-else-if="phase === 'done'">
            <div class="task-state" role="status">
              <span :class="['result-mark', { partial: result.failed }]" aria-hidden="true">{{ result.failed ? '!' : '✓' }}</span>
              <h3>{{ result.failed ? '整理完成，部分项目未成功' : changedCount ? '目录已更新' : '本次未修改文件' }}</h3>
              <div class="result-counts">
                <span><b>{{ changedCount }}</b> 移动或改名</span>
                <span v-if="result.skipped"><b>{{ result.skipped }}</b> 跳过</span>
                <span v-if="result.failed"><b>{{ result.failed }}</b> 失败</span>
              </div>
              <p>本次操作已记录，可在整理历史中查看或撤销。</p>
            </div>
          </template>
          <template v-else-if="phase === 'failed'">
            <div class="task-state"><span class="result-mark partial" aria-hidden="true">!</span><h3>整理未完成</h3><p>重新预览可检查当前目录，确认剩余需要处理的项目。</p></div>
            <p class="feedback error" role="alert">{{ error }}</p>
          </template>
          <template v-else>
            <div v-if="phase === 'confirm'" class="confirm-notice" role="status">
              <b>即将修改本剧的文件和目录</b>
              <p>请核对下方变更。字幕和 NFO 会随正片移动；整理记录可用于撤销。</p>
              <p v-if="plan.absolute_risk">{{ allowAbsolute ? '本次将按已确认的 TMDB 编号统一正片文件名。' : '本次保留正片文件名，仅执行目录整理。' }}</p>
            </div>
            <div class="section-title">
              <h3>将执行的更改</h3>
              <span v-if="loading" role="status">更新预览中…</span>
              <span v-else-if="!ready">预览未就绪</span>
              <span v-else-if="groups.length">{{ groups.length }} 项整理</span>
            </div>
            <p v-if="!selected.length" class="empty-plan">请在下方选择至少一个整理项目。</p>
            <p v-else-if="error" class="feedback error" role="alert">{{ error }} <button @click="refresh">重试预览</button></p>
            <div v-else-if="groups.length" :class="['changes', { pending: loading }]" :aria-busy="loading">
              <article v-for="(g, index) in groups" :key="index" class="change-card">
                <div class="change-heading"><span class="change-number" aria-hidden="true">{{ index + 1 }}</span><h4>{{ actionTitle(g.action) }}</h4><span v-if="!g.dir" class="change-count">{{ fileCount(g) }}</span></div>
                <div v-if="g.dir" class="path-change">
                  <div><span class="path-label">原路径</span><span class="old-path" :title="g.from">{{ g.from }}/</span></div>
                  <div><span class="path-label">新路径</span><span class="new-path" :title="g.to">{{ g.to }}/</span></div>
                </div>
                <div v-else class="file-destination"><span class="path-label">目标目录</span><span class="new-path" :title="g.to">{{ folderName(g.to) }}</span></div>
                <p v-if="g.renamed && g.action !== 'rename'" class="rename-note">同时更新 {{ g.renamed }} 个文件名</p>
                <details v-if="(g.samples || []).length" class="file-samples">
                  <summary tabindex="0">查看文件示例<span v-if="g.count > g.samples.length">（{{ g.samples.length }} / {{ g.count }}）</span></summary>
                  <div v-for="(sample, i) in g.samples" :key="i" class="sample-pair">
                    <span :title="sample.from">{{ sample.from }}</span><span class="sample-arrow" aria-hidden="true">→</span><span :title="sample.to">{{ sample.to }}</span>
                  </div>
                </details>
              </article>
            </div>
            <p v-else class="empty-plan">{{ emptyText }}</p>
            <div v-if="ready && selected.length && (plan.dir_totals || []).length" class="destination-summary">
              <span>整理后的正片位置</span>
              <div><span v-for="(row, i) in plan.dir_totals" :key="i" class="destination-chip" :title="row.dir">{{ folderName(row.dir) }} <b>{{ row.count }} 个正片</b></span></div>
            </div>

            <section v-if="plan.absolute_risk && selected.includes('rename') && phase === 'preview'" class="numbering-notice">
              <h3><span aria-hidden="true">!</span> 重命名前，请核对集号</h3>
              <p>本地使用连续集号，可能与 TMDB 的季 / 集划分不同。未确认时只整理目录，保留正片文件名。</p>
              <label class="risk-choice"><input type="checkbox" v-model="allowAbsolute" /><span>我已核对集号，允许按 TMDB 编号改名</span></label>
            </section>
            <details v-if="phase === 'preview'" class="scope-options">
              <summary tabindex="0"><span>调整整理范围</span><span class="summary-meta">{{ selected.length === actionKeys.length ? '全部项目' : `已选 ${selected.length} / ${actionKeys.length}` }}</span></summary>
              <div class="option-grid">
                <label v-for="key in actionKeys" :key="key" class="scope-option">
                  <input type="checkbox" v-model="selected" :value="key" />
                  <span><b>{{ actionTitle(key) }}</b><span>{{ ACTION_HELP[key].desc }}</span></span>
                </label>
              </div>
              <p>修改选项后会自动更新预览。</p>
            </details>
            <details v-if="notesCount" class="unchanged-notes">
              <summary tabindex="0">不会改动的项目 <span class="summary-meta">{{ notesCount }} 项</span></summary>
              <ul>
                <li v-for="(item, i) in manualItems" :key="'manual' + i">{{ manualText(item) }}</li>
                <li v-if="plan.manual_more">另有 {{ plan.manual_more }} 项需要手动处理</li>
                <li v-for="(item, i) in plan.conflicts || []" :key="'conflict' + i">目标已存在，跳过：{{ item.to || item.target || item.from || item.file || '冲突路径' }}</li>
                <li v-for="(item, i) in plan.kept || []" :key="'kept' + i">保持原名（本地集）：{{ typeof item === 'string' ? item : item.file || item.path }}</li>
                <li v-if="plan.kept_count > (plan.kept || []).length">另有 {{ plan.kept_count - (plan.kept || []).length }} 项保持原名</li>
                <li v-for="(item, i) in plan.untouched || []" :key="'deep' + i">{{ untouchedText(item) }}</li>
              </ul>
            </details>
            <p v-for="(warning, i) in warnings" :key="i" class="feedback">{{ warning }}</p>
          </template>
        </div>

        <footer class="dialog-footer">
          <button class="settings-link" :disabled="running" @click="emit('settings')">整理历史与批量操作 ↗</button>
          <div class="footer-actions">
            <template v-if="phase === 'done'">
              <button v-if="result.failed" @click="refresh">重新预览</button><button class="primary" @click="close">完成</button>
            </template>
            <template v-else-if="phase === 'failed'"><button @click="close">关闭</button><button class="primary" @click="refresh">重新预览</button></template>
            <template v-else-if="running"><button class="primary" disabled>正在整理…</button></template>
            <template v-else>
              <button v-if="phase === 'confirm'" @click="phase = 'preview'">返回预览</button>
              <button v-else @click="close">取消</button>
              <button class="primary" :disabled="!canProceed" @click="proceed">{{ loading ? '更新预览中…' : phase === 'confirm' ? '确认并开始' : '下一步：确认整理' }}</button>
            </template>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import Spinner from './Spinner.vue'
import { ACTION_HELP, basename, hintReasonText, manualText, untouchedText } from '../tvOrganizePlans.js'
import { useFocusTrap } from '../useFocusTrap.js'
import { useTvOrganizeDialog } from '../useTvOrganizeDialog.js'
const props = defineProps({ initialPlan: { type: Object, required: true }, title: { type: String, default: '' }, year: { type: [Number, String], default: '' } })
const emit = defineEmits(['close', 'finished', 'settings'])
const dialog = ref(null)
const body = ref(null)
const { selected, allowAbsolute, plan, phase, loading, ready, error, pollError, done, total, result,
  running, canProceed, refresh, proceed } = useTvOrganizeDialog(props.initialPlan, { onFinished: (value) => emit('finished', value) })
const actionKeys = Object.keys(ACTION_HELP)
const titles = {
  root: '修改剧集文件夹名', seasondir: '统一季文件夹名', wrapper: '移除多余目录层级',
  season: '将正片归入季目录', specials: '整理特别篇', extras: '归位花絮目录', rename: '统一正片文件名',
}
const actionTitle = (key) => titles[key] || ACTION_HELP[key]?.label || '整理目录'
const folderName = (path) => (basename(String(path || '').replace(/[/\\]+$/, '')) || '剧集根目录') + '/'
function fileCount(group) {
  const parts = []
  if (group.episodes) parts.push(`${group.episodes} 个正片`)
  if (group.files) parts.push(`${group.files} 个附属文件`)
  return parts.join(' · ') || `${group.count || 0} 项`
}
const warnings = computed(() => (plan.value.warnings || []).filter(warning => !(plan.value.absolute_risk && warning.startsWith('绝对集号风险：'))))
const groups = computed(() => plan.value.groups || [])
const manualItems = computed(() => (plan.value.manual || []).filter(item => !(plan.value.absolute_risk && item.reason === 'absolute')))
const notesCount = computed(() => manualItems.value.length + (plan.value.manual_more || 0)
  + (plan.value.conflicts || []).length + (plan.value.kept_count || (plan.value.kept || []).length)
  + (plan.value.untouched_count || 0))
const heading = computed(() => ({ preview: '整理剧集目录', confirm: '确认整理', running: '正在整理', done: '整理结果', failed: '整理未完成' }[phase.value]))
const changedCount = computed(() => Number(result.value?.moved || 0) + Number(result.value?.renamed || 0))
const emptyText = computed(() => plan.value.blocked ? '检测到做种保护标记，本次不修改文件或目录。可前往批量整理设置查看保护选项。'
  : plan.value.reason === 'read_only' ? '媒体库为只读，无法修改文件或目录。'
    : plan.value.absolute_risk ? '当前没有可执行的目录更改。核对集号后，可选择启用正片重命名。' : hintReasonText(plan.value) || '目录已规范，无需整理。')
function close() { if (!running.value) emit('close') }
useFocusTrap(ref(true), dialog)
let previousOverflow = ''
onMounted(() => { previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden' })
onUnmounted(() => { document.body.style.overflow = previousOverflow })
watch(phase, async () => { await nextTick(); body.value?.scrollTo({ top: 0 }) })
</script>

<style scoped>
.organize-mask { position: fixed; inset: 0; z-index: 90; display: flex; align-items: center; justify-content: center; padding: 24px; background: #000a; backdrop-filter: blur(5px); box-sizing: border-box; }
.organize-dialog { width: min(760px, 100%); max-height: min(880px, calc(100dvh - 48px)); display: flex; flex-direction: column; background: var(--jz-surface); color: var(--jz-text); border: 1px solid var(--jz-border-strong); border-radius: 16px; box-shadow: 0 24px 80px #0008; overflow: hidden; font-size: var(--jz-font-m); }
.dialog-header { display: flex; align-items: center; gap: 14px; padding: 24px 28px 20px; border-bottom: 1px solid var(--jz-border); flex-shrink: 0; }
.header-mark { display: grid; place-items: center; width: 42px; height: 42px; border-radius: var(--jz-radius-l); background: var(--jz-surface-3); color: var(--jz-text-dim); flex-shrink: 0; }
.header-mark svg { width: 24px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linejoin: round; }
.header-copy { flex: 1; min-width: 0; }
.header-copy h2 { margin: 0 0 5px; font-size: 1.2rem; line-height: 1.4; }
.header-copy p { margin: 0; color: var(--jz-text-dim); line-height: 1.5; overflow-wrap: anywhere; }
button { cursor: pointer; }
button:disabled { cursor: default; opacity: .45; }
.close-button { width: 32px; height: 32px; border: 0; padding: 0; background: transparent; color: var(--jz-text-dim); font-size: 1.5rem; flex-shrink: 0; }
.dialog-body { padding: 24px 28px; overflow-y: auto; overscroll-behavior: contain; min-height: 0; }
h3, h4, p { margin: 0; }
.section-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
.section-title h3 { font-size: var(--jz-font-m); font-weight: 600; }
.section-title > span { font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.changes { border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); background: var(--jz-bg); }
.pending { opacity: .55; }
.change-card { padding: 16px 18px; }
.change-card + .change-card { border-top: 1px solid var(--jz-border); }
.change-heading { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.change-heading h4 { font-size: var(--jz-font-m); font-weight: 500; }
.change-number { display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: var(--jz-surface-3); color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.change-count { margin-left: auto; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.path-change { display: grid; gap: 8px; padding-left: 28px; }
.path-change > div, .file-destination { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 8px; align-items: baseline; line-height: 1.5; }
.file-destination { padding-left: 28px; }
.path-label, .old-path { color: var(--jz-text-dim); }
.path-label { font-size: var(--jz-font-s); }
.old-path, .new-path { overflow-wrap: anywhere; }
.new-path { color: var(--jz-text); }
.rename-note { padding-left: 28px; margin-top: 8px; color: var(--jz-link); font-size: var(--jz-font-s); }
.file-samples { margin: 12px 0 0 28px; }
.file-samples summary { color: var(--jz-link); font-size: var(--jz-font-s); }
summary { cursor: pointer; }
.sample-pair { display: grid; grid-template-columns: minmax(0, 1fr) 18px minmax(0, 1fr); gap: 6px; padding: 10px 0; font-size: var(--jz-font-s); line-height: 1.6; overflow-wrap: anywhere; border-top: 1px solid var(--jz-border); }
.sample-pair:first-of-type { margin-top: 8px; }
.sample-pair > :first-child, .sample-arrow { color: var(--jz-text-dim); }
.destination-summary { margin-top: 14px; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.destination-summary > div { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.destination-chip { background: var(--jz-surface-2); border-radius: var(--jz-radius-s); padding: 6px 9px; overflow-wrap: anywhere; }
.destination-chip b { color: var(--jz-text); font-weight: 400; margin-left: 10px; }
.numbering-notice { border: 1px solid color-mix(in srgb, var(--jz-warn) 35%, var(--jz-border)); border-radius: var(--jz-radius-l); background: color-mix(in srgb, var(--jz-warn) 5%, var(--jz-surface)); margin-top: 20px; padding: 16px 18px; }
.numbering-notice h3 { color: var(--jz-warn); font-size: var(--jz-font-m); display: flex; align-items: center; gap: 8px; }
.numbering-notice h3 > span { display: grid; place-items: center; width: 16px; height: 16px; border: 1px solid currentColor; border-radius: 50%; font-size: var(--jz-font-s); }
.numbering-notice p { margin: 8px 0 12px; line-height: 1.7; color: var(--jz-text-dim); }
.risk-choice { display: flex; gap: 10px; align-items: flex-start; cursor: pointer; line-height: 1.6; }
input[type='checkbox'] { accent-color: var(--jz-accent); width: 16px; height: 16px; margin: 3px 0 0; flex-shrink: 0; }
.scope-options, .unchanged-notes { border-top: 1px solid var(--jz-border); margin-top: 20px; padding-top: 16px; }
.scope-options > summary, .unchanged-notes > summary { color: var(--jz-text-dim); line-height: 1.7; }
.summary-meta { float: right; color: var(--jz-text-faint); font-size: var(--jz-font-s); margin-left: 10px; }
.option-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px 0; }
.scope-option { display: flex; gap: 10px; align-items: flex-start; padding: 10px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); cursor: pointer; }
.scope-option > span { min-width: 0; }
.scope-option b { display: block; font-size: var(--jz-font-m); font-weight: 500; }
.scope-option > span > span { display: block; margin-top: 4px; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.6; }
.scope-options > p { color: var(--jz-text-faint); font-size: var(--jz-font-s); }
.unchanged-notes ul { padding-left: 20px; color: var(--jz-text-dim); line-height: 1.8; overflow-wrap: anywhere; }
.confirm-notice { padding: 16px; margin-bottom: 20px; border-radius: var(--jz-radius-m); background: var(--jz-surface-3); line-height: 1.7; }
.confirm-notice p { color: var(--jz-text-dim); margin-top: 6px; }
.empty-plan { padding: 20px; border: 1px dashed var(--jz-border-strong); border-radius: var(--jz-radius-m); color: var(--jz-text-dim); line-height: 1.7; }
.feedback { margin-top: 12px; padding: 12px; background: var(--jz-surface-2); border-radius: var(--jz-radius-m); line-height: 1.7; overflow-wrap: anywhere; }
.error { color: var(--jz-danger); }
.feedback button { margin-left: 8px; }
.task-state { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 18px; padding: 30px 12px; }
.task-state h3 { font-size: var(--jz-font-xl); }
.task-state p { color: var(--jz-text-dim); line-height: 1.7; }
.task-state progress { width: min(360px, 100%); height: 8px; accent-color: var(--jz-accent); }
.result-mark { display: grid; place-items: center; width: 44px; height: 44px; border: 1px solid var(--jz-green); border-radius: 50%; color: var(--jz-green); font-size: 1.5rem; }
.result-mark.partial { color: var(--jz-warn); border-color: var(--jz-warn); }
.result-counts { display: flex; gap: 24px; flex-wrap: wrap; justify-content: center; color: var(--jz-text-dim); }
.result-counts b { color: var(--jz-text); font-size: 1.35rem; margin-right: 6px; }
.dialog-footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 18px 28px; border-top: 1px solid var(--jz-border); background: var(--jz-surface); flex-shrink: 0; }
.settings-link { border: 0; background: transparent; padding: 6px 0; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.footer-actions { display: flex; gap: 10px; }
.footer-actions button { min-height: 40px; padding: 9px 16px; white-space: nowrap; }
.footer-actions .primary { background: var(--jz-accent); border-color: var(--jz-accent); color: var(--jz-text); font-weight: 600; }
button:focus-visible, summary:focus-visible, input:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; }
@media (max-width: 600px) {
  .organize-mask { padding: 12px; }
  .organize-dialog { max-height: calc(100dvh - 24px); border-radius: 12px; }
  .dialog-header { padding: 18px 16px; gap: 10px; }
  .header-mark { display: none; }
  .dialog-body { padding: 18px 16px; }
  .change-card, .numbering-notice { padding: 14px; }
  .change-count { margin-left: 28px; }
  .path-change, .file-destination { padding-left: 0; }
  .file-samples { margin-left: 0; }
  .rename-note { padding-left: 0; }
  .sample-pair { grid-template-columns: minmax(0, 1fr); }
  .sample-arrow { display: none; }
  .option-grid { grid-template-columns: 1fr; }
  .dialog-footer { padding: 14px 16px; flex-wrap: wrap; }
  .settings-link { order: 2; margin: 0 auto; }
  .footer-actions { width: 100%; justify-content: flex-end; }
  .footer-actions .primary { flex: 1; }
}
</style>
