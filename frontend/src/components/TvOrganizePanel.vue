<template>
  <section :id="active ? 'sec-tvorganize' : undefined" class="card-block">
    <p class="lead">
      把本库的剧集目录整理成 Plex 能正确识别的结构：季目录、文件名、特典与花絮。
      先预览（不会动任何文件），确认后执行；每次执行都会记录，可在下方整批撤销。
    </p>

    <details class="opts">
      <summary>
        整理项目
        <span class="dim">默认全部 · 已选 {{ enabled.length }}/{{ ACTION_KEYS.length }}</span>
      </summary>
      <div class="act-grid">
        <label v-for="k in ACTION_KEYS" :key="k" class="act-item">
          <input type="checkbox" v-model="acts[k]" />
          <span class="act-text">
            <b>{{ ACTION_HELP[k].label }}</b>
            <span class="act-desc">{{ ACTION_HELP[k].desc }}</span>
            <code>{{ ACTION_HELP[k].example }}</code>
          </span>
        </label>
      </div>
    </details>

    <div v-if="absShows.length" class="warn-box">
      <b>{{ absShows.length }} 部剧依赖绝对集号匹配</b>：Plex 的季/集划分可能和本地不同。
      <label class="abs-check">
        <input type="checkbox" v-model="absConfirm" @change="onAbsToggle" />
        我已核对无误，同意按 TMDB 编号改名
      </label>
    </div>

    <div class="bar">
      <button class="primary" @click="run(true)" :disabled="!!busy || !enabled.length">
        {{ busy === 'plan' ? '预览中…' : '预览整理计划' }}
      </button>
      <button @click="run(false)"
        :disabled="!!busy || !enabled.length || !checked.length || blockedByAbs"
        :class="{ danger: armRun }">
        {{ busy === 'exec' ? `整理中 ${done}/${total}…` : (armRun ? `确认执行选中 (${checked.length})` : `执行选中 (${checked.length})`) }}
      </button>
      <button v-if="busy" @click="cancel">取消</button>
    </div>
    <p v-if="blockedByAbs" class="hint warn-text">选中的剧含绝对集号风险，请先勾选上方确认。</p>
    <p v-if="armRun" class="hint warn-text">
      将对选中的剧移动目录/改正片名（花絮文件名不动），执行后自动重写 NFO/海报。再点一次执行。
    </p>

    <div v-if="plans.length" class="chips">
      <span class="chip">将执行 {{ actionPlans.length }} 部</span>
      <span v-if="execTotals.episodes" class="chip">正片 {{ execTotals.episodes }}</span>
      <span v-if="execTotals.files" class="chip">附属文件 {{ execTotals.files }}</span>
      <span v-if="allTotals.manual" class="chip warn">需手动 {{ allTotals.manual }}</span>
      <span v-if="allTotals.conflicts" class="chip warn">冲突 {{ allTotals.conflicts }}</span>
      <span v-if="allTotals.kept" class="chip">保持原名 {{ allTotals.kept }}</span>
      <span v-if="allTotals.untouched" class="chip dim">深层花絮 {{ allTotals.untouched }}</span>
      <span v-if="allTotals.blocked" class="chip warn">做种跳过 {{ allTotals.blocked }}</span>
    </div>
    <p v-if="msg" class="hint">{{ msg }}</p>
    <p v-if="!plans.length && !busy && !msg" class="hint">点「预览整理计划」查看会改什么（不会动任何文件）。</p>

    <template v-if="actionPlans.length">
      <div class="sec-head">
        <h4>将执行（{{ actionPlans.length }} 部）</h4>
        <button class="mini" @click="checkAll">{{ allChecked ? '全不选' : '全选' }}</button>
      </div>
      <div v-for="p in actionPlans" :key="p.show_id" class="plan-card">
        <div class="card-head" @click="toggle(p.show_id)">
          <input type="checkbox" :value="p.show_id" v-model="checked" @click.stop />
          <span class="caret">{{ expanded[p.show_id] ? '▾' : '▸' }}</span>
          <span class="card-title">{{ p.title }}</span>
          <span v-for="c in planActionChips(p)" :key="c" class="chip act">{{ c }}</span>
          <span v-for="f in showFlags(p)" :key="f" class="chip warn">{{ f }}</span>
        </div>
        <div v-if="expanded[p.show_id]" class="card-body">
          <div v-for="(g, i) in p.groups" :key="'g' + i" class="group-line">
            <div class="g-title">{{ groupText(g) }}</div>
            <div v-for="(l, j) in groupSamples(g).lines" :key="'s' + j" class="g-sample" :title="l.title">
              <span class="from">{{ l.from }}</span>
              <span class="arrow">→</span>
              <span class="to">{{ l.to }}</span>
            </div>
            <div v-if="groupSamples(g).more" class="g-more">
              还有 {{ groupSamples(g).more }} 项同样处理
            </div>
          </div>
          <div v-if="(p.dir_totals || []).length" class="g-title">
            执行后分布（正片）：{{ dirTotalsText(p) }}
          </div>
          <TvOrganizeNotes :plan="p" />
        </div>
      </div>
    </template>

    <details v-if="notePlans.length" class="notes">
      <summary>仅提示 · 不会执行（{{ notePlans.length }} 部）</summary>
      <div v-for="p in notePlans" :key="'n' + p.show_id" class="note-row">
        <div class="note-head" @click="toggle('n' + p.show_id)">
          <span class="caret">{{ expanded['n' + p.show_id] ? '▾' : '▸' }}</span>
          <b>{{ p.title }}</b>
          <span class="dim">{{ noteText(p) }}</span>
        </div>
        <div v-if="expanded['n' + p.show_id]" class="note-body">
          <TvOrganizeNotes :plan="p" />
        </div>
      </div>
    </details>

    <details class="undo" :open="!!restoreMsg">
      <summary>整理历史 / 撤销（{{ batches.length }} 个批次）</summary>
      <p class="hint">
        每次执行都会记录每一步，可在这里整批撤销。选批次 → 预览撤销 → 执行；
        源文件缺失或原路径被占用时会跳过并提示。
      </p>
      <div class="bar">
        <select v-model="batchId">
          <option value="">（最近一次批次）</option>
          <option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">
            {{ fmtBatch(b) }}
          </option>
        </select>
      </div>
      <div class="chips">
        <button v-for="k in KIND_OPTS" :key="k.value" type="button" class="chip toggle"
          :class="{ on: kinds[k.value] }" @click="kinds[k.value] = !kinds[k.value]">{{ k.label }}</button>
        <button type="button" class="mini" @click="toggleAllKinds">{{ allKinds ? '全不选' : '全选' }}</button>
      </div>
      <div class="bar">
        <button @click="restore(true)" :disabled="!!busy2 || !selectedKinds.length">
          {{ busy2 === 'plan' ? '预览中…' : '预览撤销' }}
        </button>
        <button @click="restore(false)" :disabled="!!busy2 || !selectedKinds.length"
          :class="{ danger: armUndo }">
          {{ busy2 === 'exec' ? `撤销中 ${done2}/${total2}…` : (armUndo ? '确认执行撤销' : '执行撤销') }}
        </button>
        <button v-if="busy2" @click="cancelRestore">取消</button>
        <span class="dim">{{ restoreMsg }}</span>
      </div>
      <p v-if="armUndo" class="hint warn-text">将按记录把上述文件反向搬回原路径（不改文件名）。再点一次执行。</p>
      <table v-if="restorePlans.length" class="org-table">
        <thead>
          <tr><th>剧</th><th>还原</th><th>冲突</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in restorePlans" :key="p.show_id">
            <td>{{ p.title }}</td>
            <td>{{ (p.moves || []).length }}</td>
            <td>{{ (p.conflicts || []).length }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </section>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import {
  ACTION_HELP, actionTotals, defaultChecked, dirTotalsText, groupSamples, groupText,
  noteText, planActionChips, riskShowIds, showFlags, splitPlans,
} from '../tvOrganizePlans.js'
import TvOrganizeNotes from './TvOrganizeNotes.vue'

const props = defineProps({
  library: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed', 'status'])

const ACTION_KEYS = Object.keys(ACTION_HELP)
const acts = ref(Object.fromEntries(ACTION_KEYS.map((k) => [k, true])))
const enabled = computed(() => ACTION_KEYS.filter((k) => acts.value[k]))
const busy = ref(null)
const msg = ref('')
const plans = ref([])
const checked = ref([])
const expanded = ref({})
const done = ref(0)
const total = ref(0)
const armRun = ref(false)
let job = ''
let timer = null

// 预览分区：有动作=将执行；无动作但有提示=仅提示（不再混在一张表里）
const planParts = computed(() => splitPlans(plans.value))
const actionPlans = computed(() => planParts.value.actionPlans)
const notePlans = computed(() => planParts.value.notePlans)
const execTotals = computed(() => actionTotals(actionPlans.value))
const allTotals = computed(() => actionTotals(plans.value))
const allChecked = computed(() => actionPlans.value.length > 0
  && actionPlans.value.every((p) => checked.value.includes(p.show_id)))

// 绝对集号风险确认（与详情页弹窗口径一致）：确认后预览/执行才包含正片改名
const absConfirm = ref(false)
const absShows = computed(() => riskShowIds(plans.value))
const blockedByAbs = computed(() => !absConfirm.value && checked.value.some(
  (id) => plans.value.some((p) => p.show_id === id && p.absolute_risk)))

function onAbsToggle() {
  if (busy.value) return
  // 切换确认状态后重新预览：计划列表与默认勾选（含风险剧）随之刷新
  run(true)
}

function toggle(key) {
  expanded.value = { ...expanded.value, [key]: !expanded.value[key] }
}

function checkAll() {
  checked.value = allChecked.value ? [] : actionPlans.value.map((p) => p.show_id)
}

const KIND_OPTS = [
  { value: 'extra', label: '花絮' },
  { value: 'episode', label: '剧集' },
  { value: 'file', label: '附属文件' },
  { value: 'dir', label: '目录' },
  { value: 'rmdir', label: '空目录' },
]
const batches = ref([])
const batchId = ref('')
const kinds = ref({ extra: true, episode: true, file: true, dir: true, rmdir: true })
const allKinds = computed(() => KIND_OPTS.every((k) => kinds.value[k.value]))
const busy2 = ref(null)
const restoreMsg = ref('')
const restorePlans = ref([])
const done2 = ref(0)
const total2 = ref(0)
const armUndo = ref(false)
let job2 = ''
let timer2 = null

const selectedKinds = computed(() => Object.entries(kinds.value)
  .filter(([, v]) => v).map(([k]) => k))

function toggleAllKinds() {
  const want = !allKinds.value
  for (const k of KIND_OPTS) kinds.value[k.value] = want
}

function fmtBatch(b) {
  const t = b.updated_at ? new Date(b.updated_at * 1000).toLocaleString() : ''
  const undone = Number(b.undone || 0)
  return `${b.batch_id} · ${b.total} 条${undone ? `（已撤销 ${undone}）` : ''}${t ? ' · ' + t : ''}`
}

async function loadHistory() {
  try {
    const d = await api('/api/jobs/tv-organize/history?limit=30')
    batches.value = d.batches || []
  } catch (e) { /* 忽略 */ }
}

async function run(dryRun) {
  if (!enabled.value.length) return
  if (!dryRun && !armRun.value) {
    armRun.value = true
    msg.value = ''
    return
  }
  armRun.value = false
  busy.value = dryRun ? 'plan' : 'exec'
  msg.value = ''
  const absIds = absShows.value      // 清空 plans 前先取风险剧 id（预览时也要带上）
  if (dryRun) {
    plans.value = []
    checked.value = []
  }
  done.value = 0
  total.value = 0
  try {
    const body = {
      library_id: props.library.id,
      actions: enabled.value,
      dry_run: dryRun,
    }
    if (!dryRun) {
      body.ids = checked.value
      body.allow_absolute_shows = checked.value   // 勾选=显式同意该剧（含绝对集号风险剧）执行
    } else if (absConfirm.value && absIds.length) {
      body.allow_absolute_shows = absIds           // 已确认：预览即含正片改名（与执行口径一致）
    }
    const d = await api('/api/jobs/tv-organize', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    job = d.job_id
    clearInterval(timer)
    timer = setInterval(poll, 1200)
  } catch (e) {
    msg.value = '启动失败：' + e.message
    busy.value = null
  }
}

async function poll() {
  try {
    const j = await api('/api/jobs/tv-organize/' + job)
    done.value = j.done || 0
    total.value = j.total || 0
    if (j.state === 'done') {
      clearInterval(timer); timer = null; busy.value = null
      const s = j.summary || {}
      if (s.dry_run) {
        plans.value = s.plans || []
        checked.value = actionPlans.value
          .filter((p) => defaultChecked(p, { allowAbs: absConfirm.value }))
          .map((p) => p.show_id)
        msg.value = plans.value.length
          ? '' : '本库没有需要整理的内容（目录与文件名都规范）。'
        emit('status', { plans: actionPlans.value.length })
      } else {
        msg.value = `完成：改名/移动 ${s.moved || 0}，跳过 ${s.skipped || 0}`
          + (s.failed ? `，失败 ${s.failed}` : '')
        emit('status', { plans: 0, executed: s.moved || 0 })
        emit('changed')
        loadHistory()
      }
    } else if (j.state === 'failed' || j.state === 'cancelled') {
      clearInterval(timer); timer = null; busy.value = null
      msg.value = j.error || j.state
    }
  } catch (e) { /* 下一轮 */ }
}

async function restore(dryRun) {
  if (!selectedKinds.value.length) return
  if (!dryRun && !armUndo.value) {
    armUndo.value = true
    restoreMsg.value = ''
    return
  }
  armUndo.value = false
  busy2.value = dryRun ? 'plan' : 'exec'
  restoreMsg.value = ''
  restorePlans.value = []
  done2.value = 0
  total2.value = 0
  try {
    const d = await api('/api/jobs/tv-organize-restore', {
      method: 'POST',
      body: JSON.stringify({
        batch_id: batchId.value || null,
        library_id: props.library.id,
        kinds: selectedKinds.value,
        dry_run: dryRun,
      }),
    })
    job2 = d.job_id
    clearInterval(timer2)
    timer2 = setInterval(pollRestore, 1200)
  } catch (e) {
    restoreMsg.value = '启动失败：' + e.message
    busy2.value = null
  }
}

async function pollRestore() {
  try {
    const j = await api('/api/jobs/tv-organize-restore/' + job2)
    done2.value = j.done || 0
    total2.value = j.total || 0
    if (j.state === 'done') {
      clearInterval(timer2); timer2 = null; busy2.value = null
      const s = j.summary || {}
      restorePlans.value = s.plans || []
      if (s.dry_run) {
        restoreMsg.value = `批次 ${s.batch_id || '（最近）'}：可还原 ${s.total || 0} 条，`
          + `冲突 ${s.conflicts || 0}`
      } else {
        restoreMsg.value = `完成：还原 ${s.restored || 0}，跳过 ${s.skipped || 0}`
          + (s.failed ? `，失败 ${s.failed}` : '')
        emit('changed')
        loadHistory()
      }
    } else if (j.state === 'failed' || j.state === 'cancelled') {
      clearInterval(timer2); timer2 = null; busy2.value = null
      restoreMsg.value = j.error || j.state
    }
  } catch (e) { /* 下一轮 */ }
}

async function cancel() {
  if (!job) return
  try { await api(`/api/jobs/tv-organize/${job}/cancel`, { method: 'POST' }) } catch (e) { /* 忽略 */ }
}
async function cancelRestore() {
  if (!job2) return
  try { await api(`/api/jobs/tv-organize-restore/${job2}/cancel`, { method: 'POST' }) } catch (e) { /* 忽略 */ }
}

onMounted(loadHistory)
onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (timer2) clearInterval(timer2)
})
</script>
<style scoped>
.lead { color: #999; font-size: 0.8125rem; line-height: 1.6; margin: 0 0 10px; }
.hint { color: #777; font-size: 0.8125rem; margin: 6px 0; }
.dim { color: #777; }
.warn-text { color: #e0a63c; }
.bar { padding: 0; margin: 8px 0; flex-wrap: wrap; align-items: center; }
.bar .dim { font-size: 0.8125rem; }

/* 整理项目：折叠 + 自适应网格（每项独占一格，说明独立行不折标签） */
.opts { border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); padding: 8px 10px; margin: 0 0 10px; }
.opts summary { cursor: pointer; color: #bbb; font-size: 0.8125rem; }
.opts summary .dim { margin-left: 8px; }
.act-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 8px 18px; margin-top: 8px; }
.act-item { display: flex; gap: 8px; align-items: flex-start; font-size: 0.8125rem; cursor: pointer; }
.act-item input { margin-top: 3px; flex: none; }
.act-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.act-text b { color: #ddd; font-weight: 500; white-space: nowrap; }
.act-desc { color: #888; }
.act-text code { color: #7fa8cc; font-size: 0.75rem; overflow-wrap: anywhere; }

.warn-box { color: #e0a63c; background: #241d0e; border: 1px solid #6b5410; border-radius: var(--jz-radius-m); padding: 8px 10px; font-size: 0.8125rem; margin: 0 0 10px; line-height: 1.6; }
.abs-check { display: block; margin-top: 4px; color: #d8b25a; }

/* 结果 chips：按钮/状态分行，数字一目了然 */
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 8px 0; }
.chip { font-size: 0.75rem; border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-pill); padding: 1px 10px; color: #aaa; white-space: nowrap; }
.chip.warn { color: var(--jz-warn); border-color: #6b5410; }
.chip.act { color: var(--jz-link); border-color: #34506b; }
.chip.dim { color: #777; }
.chip.toggle { cursor: pointer; background: transparent; }
.chip.toggle.on { color: var(--jz-green); border-color: #3a5a1e; }
button.primary { background: #2b6cb0; border-color: #2b6cb0; color: #fff; }
button.primary:hover:not(:disabled) { background: #3580cc; }
button.primary:disabled { background: #333; border-color: #444; color: #777; }
button.danger { border-color: #6e2b2b; color: #ff8a8a; }
button.mini { font-size: 0.75rem; padding: 2px 10px; }

.sec-head { display: flex; align-items: center; gap: 8px; margin: 12px 0 6px; }
.sec-head h4 { margin: 0; font-size: 0.875rem; color: #ccc; }

/* 行卡片：剧名 + 动作 chips + 徽标；展开后明细 */
.plan-card { border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); background: #1a1a1a; margin-bottom: 6px; }
.plan-card:hover { border-color: #3a3a3a; }
.card-head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; cursor: pointer; flex-wrap: wrap; }
.card-head input { flex: none; }
.card-title { color: #ddd; font-size: 0.875rem; }
.card-body { padding: 4px 12px 10px 36px; border-top: 1px solid #262626; }
.caret { color: #888; }
.group-line { margin: 6px 0 8px; }
.g-title { color: #bbb; font-size: 0.8125rem; margin: 6px 0 2px; }
.g-sample { color: #8a8a8a; font-size: 0.8125rem; padding-left: 12px; overflow-wrap: anywhere; }
.g-sample .from { color: #9a9a9a; }
.g-sample .arrow { margin: 0 6px; }
.g-sample .to { color: #cfcfcf; }
.g-more { color: #666; font-size: 0.8125rem; padding-left: 12px; }

/* 仅提示区：默认折叠，一行一句人话 */
.notes { margin-top: 12px; border-top: 1px dashed #3a3a3a; padding-top: 8px; }
.notes summary { cursor: pointer; color: #bbb; font-size: 0.8125rem; }
.note-row { border-bottom: 1px solid #242424; }
.note-head { display: flex; gap: 8px; align-items: baseline; padding: 7px 2px; cursor: pointer; font-size: 0.8125rem; flex-wrap: wrap; }
.note-head b { color: #ccc; font-weight: 500; }
.note-head .dim { color: #888; }
.note-body { padding: 0 0 8px 20px; }

.org-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; margin-top: 8px; }
.org-table th, .org-table td { padding: 6px 8px; border-bottom: 1px solid var(--jz-border); text-align: left; vertical-align: top; }
.org-table th { color: #888; font-weight: normal; }

.undo { margin-top: 14px; border-top: 1px solid var(--jz-border); padding-top: 10px; }
.undo summary { cursor: pointer; color: #bbb; }
</style>
