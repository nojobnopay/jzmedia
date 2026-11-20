<template>
  <section class="assistant-change" data-assistant-diagnosis aria-label="在线资源诊断">
    <h4><AppIcon name="settings" :size="16" />在线资源诊断</h4>
    <p v-if="cancelled" role="status">已取消诊断。</p>
    <template v-else-if="needsUnlock">
      <p>读取视频源配置和测试接口需要管理权限。</p>
      <JzButton size="compact" @click="emit('unlock', question)">验证并诊断</JzButton>
    </template>
    <template v-else>
      <p v-if="loading" role="status">正在读取已配置的视频源…</p>
      <template v-if="sources.length">
        <label for="assistant-diagnosis-source">要诊断的视频源</label>
        <select id="assistant-diagnosis-source" v-model="sourceId" :disabled="saving">
          <option disabled value="">请选择来源</option>
          <option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }}{{ duplicateNames.has(source.name) ? ' · ' + source.id : '' }}</option>
        </select>
        <p v-if="!sourceId">请选择这次无法播放的来源。</p>
      </template>
      <p v-if="checking" role="status">{{ comparing ? '正在通过临时直连测试本集…' : '我正在帮你分析问题原因，请稍等…' }}</p>
      <p v-if="report" role="status">{{ report.answer }}</p>
      <details v-if="report?.evidence?.length" data-diagnosis-evidence>
        <summary>查看检测依据（{{ report.evidence.length }}）</summary>
        <ul>
          <li v-for="(entry, index) in report.evidence" :key="index">
            <strong>{{ entry.label }}</strong> · {{ outcomeLabel(entry.outcome) }}
            <span v-if="entry.detail"> {{ entry.detail }}</span>
            <small v-if="checkTime(entry.checked_at)"> {{ checkTime(entry.checked_at) }}</small>
            <a v-if="safeHomepage(entry.page_url)" :href="safeHomepage(entry.page_url)" target="_blank" rel="noopener noreferrer">查看来源网页</a>
          </li>
        </ul>
      </details>
      <template v-if="proposal">
        <p>{{ directProposal ? '是否只将此来源的视频播放出口改为直连？' : '是否为此来源更新接口和下列允许主机？' }}</p>
        <dl>
          <dt>{{ directProposal ? '当前播放出口' : '当前接口' }}</dt><dd>{{ directProposal ? proposal.old_label || '代理' : proposal.old_url }}</dd>
          <dt>{{ directProposal ? '新的播放出口' : '新的接口' }}</dt><dd>{{ directProposal ? proposal.new_label || '直连' : proposal.new_url }}</dd>
        </dl>
        <ul v-if="proposal.changes?.length">
          <li v-for="change in proposal.changes" :key="change.old_host">{{ migrationKinds[change.kind] }} · {{ change.old_host }} 换为 {{ change.new_host }}</li>
        </ul>
        <p>保存后，此来源的在线播放会话将关闭，需要重新播放。</p>
        <p v-if="access?.blocked" role="status">{{ access.blocked }}</p>
        <div class="assistant-actions">
          <JzButton variant="primary" :disabled="!!access?.blocked || saving" :loading="saving" @click="confirm">同意修改</JzButton>
          <JzButton variant="ghost" :disabled="saving" @click="cancel">取消</JzButton>
        </div>
      </template>
      <p v-if="saving" role="status">正在保存并复测此来源…</p>
      <p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <div v-if="!checking && !saving && !loading" class="assistant-actions">
        <JzButton v-if="report?.can_compare_direct && !proposal" size="compact" @click="diagnose(true)">允许临时直连测试本集</JzButton>
        <JzButton v-if="applied && context?.source_id === sourceId && context.replay" size="compact" variant="primary" @click="emit('replay', context.replay)">重新播放</JzButton>
        <JzButton v-if="!proposal && sourceId" size="compact" @click="diagnose()">重新诊断</JzButton>
        <JzButton v-else-if="error && !sources.length" size="compact" @click="load">重新加载</JzButton>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { api } from '../api.js'
import { migrationKinds, safeHomepage, sourceMatchesTemplate } from '../useOnlineHostMigration.js'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'

const props = defineProps({ question: String, templateId: String, needsUnlock: Boolean, context: Object, access: Object })
const emit = defineEmits(['busy', 'unlock', 'replay', 'summary'])
const sources = ref([]), sourceId = ref(''), report = ref(null), proposal = ref(null)
const loading = ref(false), checking = ref(false), comparing = ref(false), saving = ref(false), applied = ref(false)
const error = ref(''), notice = ref(''), cancelled = ref(false)
const directProposal = computed(() => proposal.value?.kind === 'media_direct')
const duplicateNames = computed(() => {
  const seen = new Set(), duplicates = new Set()
  for (const { name } of sources.value) { if (seen.has(name)) duplicates.add(name); seen.add(name) }
  return duplicates
})
let request, expiry, disposed = false
const outcomes = { ok: '正常', success: '成功', failed: '失败', blocked: '受限', skipped: '跳过', unknown: '未确认', warning: '需留意', info: '信息' }
function outcomeLabel(value) { return outcomes[value] || value }
function checkTime(value) {
  if (!value) return ''
  const date = new Date(typeof value === 'number' ? value * 1000 : value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : ''
}
function resetResult() {
  clearTimeout(expiry)
  report.value = null; proposal.value = null; applied.value = false; notice.value = ''; error.value = ''
}
function expire() {
  proposal.value = null
  notice.value = '这份方案已过期，请重新诊断再确认。'
}
async function load() {
  if (props.needsUnlock) return
  request?.abort()
  const control = request = new AbortController()
  loading.value = true; error.value = ''
  try {
    const { items } = await api('/api/online/sources', { signal: control.signal, cache: 'no-store' })
    if (control.signal.aborted) return
    sources.value = items
    if (!items.length) { notice.value = '尚未配置视频源，请先到设置添加来源。'; return }
    let choices = items.filter(row => row.name && props.question?.includes(row.name))
    if (props.templateId && !choices.length) {
      const catalogue = await api('/api/online/source-templates', { signal: control.signal })
      if (control.signal.aborted) return
      const operator = catalogue.items.find(item => item.id === props.templateId)
      choices = operator ? items.filter(row => sourceMatchesTemplate(row, operator)) : []
    } else if (!choices.length) {
      choices = props.context?.source_id ? items.filter(row => row.id === props.context.source_id) : items
    }
    const contextual = choices.find(row => row.id === props.context?.source_id)
    if (choices.length === 1 || contextual) sourceId.value = (contextual || choices[0]).id
  } catch {
    if (!control.signal.aborted) error.value = '无法读取视频源，请检查连接或重新验证管理权限。'
  } finally { if (!control.signal.aborted) loading.value = false }
}
async function diagnose(compareDirect = false) {
  if (!sourceId.value || props.needsUnlock || saving.value) return
  request?.abort()
  const control = request = new AbortController()
  loading.value = false; checking.value = true; comparing.value = compareDirect
  resetResult()
  const body = { question: /https?:\/\/|(?:密码|令牌|api[_ -]?key|token)\s*[:：=]/i.test(props.question) ? '为什么在线资源无法播放？' : props.question }
  if (props.context?.source_id === sourceId.value) {
    for (const key of ['external_id', 'item_id', 'episode_id', 'line_id', 'error_code', 'cache_only']) {
      const value = props.context[key]
      if (value != null && value !== '') body[key] = value
    }
  }
  if (compareDirect) body.compare_direct = true
  try {
    const result = await api('/api/online/sources/' + encodeURIComponent(sourceId.value) + '/diagnosis', {
      method: 'POST', signal: control.signal, timeout: 65000, body: JSON.stringify(body),
    })
    if (control.signal.aborted) return
    if (typeof result?.answer !== 'string' || !Array.isArray(result.evidence)) throw new Error()
    report.value = result
    emit('summary', result.answer)
    const p = result.proposal
    if (!p) return
    if (!p.id || !['api', 'media_direct'].includes(p.kind || 'api') ||
      p.kind !== 'media_direct' && (!p.old_url || !p.new_url) || !Number.isFinite(p.expires_at)) throw new Error()
    const remaining = p.expires_at * 1000 - Date.now()
    if (remaining <= 0) expire()
    else { proposal.value = p; expiry = setTimeout(expire, Math.min(remaining, 2147483647)) }
  } catch (failure) {
    if (!control.signal.aborted) error.value = failure.status === 409 ? '来源配置已变化，请重新诊断。'
      : failure.status === 429 ? '上一次诊断还在进行，请稍后重新诊断。' : '未获得完整诊断结果，请检查连接和管理权限后重试。'
  } finally { if (!control.signal.aborted) checking.value = false }
}
async function confirm() {
  const p = proposal.value
  if (!p || props.needsUnlock || props.access?.blocked || saving.value) return
  if (p.expires_at * 1000 <= Date.now()) { expire(); return }
  clearTimeout(expiry)
  const id = sourceId.value
  saving.value = true; notice.value = ''; error.value = ''
  props.access?.setBusy(true); emit('busy', true)
  try {
    const result = await api('/api/online/sources/' + encodeURIComponent(id) + '/diagnosis/' + encodeURIComponent(p.id) + '/confirm', {
      method: 'POST', timeout: 65000, body: '{}',
    })
    if (disposed) return
    if (result?.status !== 'applied' || typeof result.answer !== 'string') throw new Error()
    proposal.value = null; report.value = result; applied.value = true
    emit('summary', result.answer)
    try {
      const list = await api('/api/online/sources', { cache: 'no-store' })
      if (disposed) return
      sources.value = list.items
      const saved = list.items.find(row => row.id === id)
      if (!saved) throw new Error()
      props.access?.saved(saved)
    } catch { if (!disposed) notice.value = '保存已完成，暂时无法刷新来源列表。' }
  } catch (failure) {
    if (disposed) return
    proposal.value = null
    error.value = failure.status === 409 ? '来源配置或方案已变化，请重新诊断后确认。' : '保存结果尚未确认，请重新诊断以核对当前配置。'
  } finally {
    saving.value = false; props.access?.setBusy(false); emit('busy', false)
  }
}
function cancel() {
  if (saving.value) return
  request?.abort(); clearTimeout(expiry); resetResult()
  cancelled.value = true; checking.value = false; loading.value = false
  emit('summary', '已取消诊断。')
}
function handleReply(agree) {
  if (!agree) cancel()
  else if (proposal.value) void confirm()
  else if (!saving.value) notice.value = checking.value ? '诊断仍在进行，请等检测结果出来后再确认。' : '目前没有等待确认的方案。'
}
defineExpose({ handleReply })
watch(sourceId, () => { if (sourceId.value) void diagnose() })
onMounted(load)
onScopeDispose(() => { disposed = true; request?.abort(); clearTimeout(expiry) })
</script>

<style scoped>
details { overflow-wrap: anywhere; }
summary { cursor: pointer; }
ul { padding-inline-start: 1.25em; margin: 0; }
details li { margin-top: .4em; }
details strong { display: inline; }
details a { margin-inline-start: .4em; }
</style>
