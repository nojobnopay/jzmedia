<template>
  <section :id="active ? 'sec-tvorganize' : undefined" class="card-block">
    <h3>剧集目录整理 <span class="fhint">目录只移动，正片按 Plex 模板改名</span></h3>
    <p class="hint">
      剧根改名（`Breaking.Bad.2008 → 绝命毒师 (2008)`）；季目录规范化（`season 1`/`S04` → `Season 01`）；
      包装层拍平；剧根散集补 `Season NN/`；特典（OVA/OAD/SP）归位 `Season 00/`；
      花絮类型目录整目录上移（仅深度 ≤2，更深只报告，花絮文件名不动）；
      正片统一命名（`剧名-S01E01-集名.ext`，同集多版本加 `-V2`）。
      执行逐条留痕可撤销；含 .torrent 的剧默认跳过。展开每剧可看具体改动与「需手动处理」项；
      勾选「绝对集号风险」剧即表示同意按其 TMDB 编号改名。
    </p>
    <div class="bar">
      <label v-for="k in ACTION_KEYS" :key="k">
        <input type="checkbox" v-model="acts[k]" /> {{ ACTION_LABELS[k] }}
      </label>
    </div>
    <div class="bar">
      <button @click="run(true)" :disabled="!!busy || !enabled.length">
        {{ busy === 'plan' ? '预览中…' : '预览整理计划' }}
      </button>
      <button @click="run(false)" :disabled="!!busy || !enabled.length || !checked.length"
        :class="{ danger: armRun }">
        {{ busy === 'exec' ? `整理中 ${done}/${total}…` : (armRun ? `确认执行选中 (${checked.length})` : `执行选中 (${checked.length})`) }}
      </button>
      <button v-if="busy" @click="cancel">取消</button>
      <button v-if="plans.length" @click="checkAll">{{ allChecked ? '全不选' : '全选' }}</button>
      <label v-if="plans.length" class="dim">
        <input type="checkbox" v-model="onlyChanged" /> 只看有改动
      </label>
      <span>{{ msg }}</span>
    </div>
    <p v-if="armRun" class="hint warn-text">
      将对勾选的剧移动目录/改正片名（花絮文件名不动），执行后自动重写 NFO/海报。再点一次执行。
    </p>
    <table v-if="visiblePlans.length" class="org-table">
      <thead>
        <tr>
          <th class="chk"></th><th>剧</th><th>动作</th><th>需手动</th><th>冲突</th><th>备注</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="p in visiblePlans" :key="p.show_id">
          <tr :class="{ blocked: p.blocked }">
            <td class="chk">
              <input type="checkbox" :value="p.show_id" v-model="checked" />
            </td>
            <td class="expand" @click="toggle(p.show_id)">
              <span class="caret">{{ expanded[p.show_id] ? '▾' : '▸' }}</span>
              {{ p.title }}
              <span v-for="f in showFlags(p)" :key="f" class="flag">{{ f }}</span>
            </td>
            <td class="dim">{{ showTotalText(p) || '无改动' }}</td>
            <td>{{ (p.manual || []).length + (p.manual_more || 0) }}</td>
            <td>{{ (p.conflicts || []).length }}</td>
            <td class="dim">{{ (p.warnings || []).join('；') }}</td>
          </tr>
          <tr v-if="expanded[p.show_id]" class="detail-row">
            <td></td>
            <td colspan="5">
              <div v-for="(g, i) in p.groups" :key="'g' + i" class="group-line">
                <div class="g-title">{{ groupText(g) }}</div>
                <div v-for="(l, j) in groupSamples(g).lines" :key="'s' + j" class="g-sample">
                  <span class="from">{{ l.from }}</span>
                  <span class="arrow">→</span>
                  <span class="to">{{ l.to }}</span>
                </div>
                <div v-if="groupSamples(g).more" class="g-more">
                  还有 {{ groupSamples(g).more }} 项同样处理
                </div>
              </div>
              <div v-if="(p.untouched || []).length" class="manual-block">
                <div class="g-title warn-text">未动（更深层花絮，建议手动整理）</div>
                <div v-for="(t, i) in p.untouched" :key="'u' + i" class="g-sample">
                  {{ untouchedText(t) }}
                </div>
              </div>
              <div v-if="(p.manual || []).length" class="manual-block">
                <div class="g-title warn-text">需手动处理</div>
                <div v-for="(m, i) in p.manual" :key="'m' + i" class="g-sample" :title="m.suggestion">
                  {{ manualText(m) }}
                </div>
                <div v-if="p.manual_more" class="g-more">还有 {{ p.manual_more }} 条同样需手动处理</div>
              </div>
              <div v-if="(p.conflicts || []).length" class="manual-block">
                <div class="g-title warn-text">冲突（不会执行）</div>
                <div v-for="(c, i) in p.conflicts" :key="'c' + i" class="g-sample">
                  {{ basename(c.from) }} → {{ basename(c.to) }}：{{ c.reason }}
                </div>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <p v-if="!visiblePlans.length && !busy && msg" class="hint">{{ msg }}</p>

    <details class="undo" :open="!!restoreMsg">
      <summary>整理历史 / 撤销（{{ batches.length }} 个批次）</summary>
      <p class="hint">
        每次执行整理都会逐条留痕（from → to）。选择批次可把移动**反向搬回**；
        冲突（源缺失/原路径被占）只报告不执行。撤销本身也会留痕，可再次撤销=重做。
      </p>
      <div class="bar">
        <select v-model="batchId">
          <option value="">（最近一次批次）</option>
          <option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">
            {{ fmtBatch(b) }}
          </option>
        </select>
        <label v-for="k in KIND_OPTS" :key="k.value" class="dim">
          <input type="checkbox" v-model="kinds[k.value]" /> {{ k.label }}
        </label>
      </div>
      <div class="bar">
        <button @click="restore(true)" :disabled="!!busy2">
          {{ busy2 === 'plan' ? '预览中…' : '预览撤销' }}
        </button>
        <button @click="restore(false)" :disabled="!!busy2"
          :class="{ danger: armUndo }">
          {{ busy2 === 'exec' ? `撤销中 ${done2}/${total2}…` : (armUndo ? '确认执行撤销' : '执行撤销') }}
        </button>
        <button v-if="busy2" @click="cancelRestore">取消</button>
        <span>{{ restoreMsg }}</span>
      </div>
      <p v-if="armUndo" class="hint warn-text">将按审计把上述文件反向搬回原路径（不改文件名）。再点一次执行。</p>
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
  ACTION_LABELS, basename, defaultChecked, groupSamples, groupText, manualText,
  planTotal, showFlags, showTotalText, untouchedText,
} from '../tvOrganizePlans.js'

const props = defineProps({
  media: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const ACTION_KEYS = ['root', 'seasondir', 'wrapper', 'season', 'specials', 'extras', 'rename']
const acts = ref(Object.fromEntries(ACTION_KEYS.map((k) => [k, true])))
const enabled = computed(() => ACTION_KEYS.filter((k) => acts.value[k]))
const busy = ref(null)
const msg = ref('')
const plans = ref([])
const checked = ref([])
const expanded = ref({})
const onlyChanged = ref(false)
const done = ref(0)
const total = ref(0)
const armRun = ref(false)
let job = ''
let timer = null

const visiblePlans = computed(() => (onlyChanged.value
  ? plans.value.filter((p) => planTotal(p) > 0)
  : plans.value))
const allChecked = computed(() => plans.value.length > 0
  && plans.value.every((p) => checked.value.includes(p.show_id)))

function toggle(showId) {
  expanded.value = { ...expanded.value, [showId]: !expanded.value[showId] }
}

function checkAll() {
  checked.value = allChecked.value ? [] : plans.value.map((p) => p.show_id)
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
  if (dryRun) {
    plans.value = []
    checked.value = []
  }
  done.value = 0
  total.value = 0
  try {
    const body = {
      media_library_id: props.media.id,
      actions: enabled.value,
      dry_run: dryRun,
    }
    if (!dryRun) {
      body.ids = checked.value
      body.allow_absolute_shows = checked.value   // 勾选=显式同意该剧（含绝对集号风险剧）执行
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
        checked.value = plans.value.filter(defaultChecked).map((p) => p.show_id)
        const c = s.counts || {}
        msg.value = `计划：` + ACTION_KEYS.filter((k) => c[k])
          .map((k) => `${ACTION_LABELS[k]} ${c[k]}`).join('，')
          + `；散文件未动 ${s.untouched || 0}，需手动 ${s.manual || 0}，`
          + `绝对集号风险剧 ${s.absolute || 0}，冲突 ${s.conflicts || 0}，做种跳过 ${s.blocked || 0} 部`
      } else {
        msg.value = `完成：改名/移动 ${s.moved || 0}，跳过 ${s.skipped || 0}`
          + (s.failed ? `，失败 ${s.failed}` : '')
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
        media_library_id: props.media.id,
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
.org-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; margin-top: 8px; }
.org-table th, .org-table td { padding: 6px 8px; border-bottom: 1px solid #2c2c2c; text-align: left; vertical-align: top; }
.org-table th { color: #888; font-weight: normal; }
.org-table tr.blocked { color: #b98a00; }
.org-table td.chk, .org-table th.chk { width: 26px; }
.expand { cursor: pointer; user-select: none; }
.caret { color: #888; margin-right: 2px; }
.flag { margin-left: 6px; font-size: 0.6875rem; color: #b98a00; border: 1px solid #6b5410; border-radius: 3px; padding: 0 4px; }
.detail-row td { background: #141414; }
.group-line, .manual-block { margin: 4px 0 8px; }
.g-title { color: #bbb; }
.g-sample { color: #8a8a8a; padding-left: 12px; }
.g-sample .from { color: #9a9a9a; }
.g-sample .arrow { margin: 0 6px; }
.g-sample .to { color: #cfcfcf; }
.g-more { color: #666; padding-left: 12px; }
.dim { color: #777; }
.warn-text { color: #ffb300; }
button.danger { border-color: #e50914; color: #ff8a8a; }
.undo { margin-top: 14px; border-top: 1px solid #2c2c2c; padding-top: 10px; }
.undo summary { cursor: pointer; color: #bbb; }
</style>
