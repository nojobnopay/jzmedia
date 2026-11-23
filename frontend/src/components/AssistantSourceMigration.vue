<template>
  <section class="assistant-change" aria-label="在线视频源换址" data-assistant-source-migration>
    <p v-if="finished" role="status">{{ finished }}</p>
    <template v-else>
      <h4><AppIcon name="settings" :size="16" />更新在线视频源接口</h4>
      <p v-if="loading" role="status">正在读取视频源…</p>
      <p v-else-if="listError" role="alert">{{ listError }} <JzButton size="compact" @click="load">重新加载</JzButton></p>
      <template v-else-if="sources.length">
        <label for="assistant-source-target">要更新的已有来源</label>
        <select id="assistant-source-target" v-model="sourceId" :disabled="saving" @change="automaticSource = ''">
          <option disabled value="">请选择来源</option>
          <option v-for="source in sources" :key="source.id" :value="source.id">{{ sourceLabel(source) }}</option>
        </select>
        <label for="assistant-source-template">新接口取自</label>
        <select id="assistant-source-template" v-model="templateId" :disabled="saving" @change="choseDestination = true">
          <option value="">填写接口地址</option>
          <option v-for="item in templates" :key="item.id" :value="item.id">{{ item.name }}模板</option>
        </select>
        <template v-if="!templateId">
          <label for="assistant-source-url">新接口地址</label>
          <input id="assistant-source-url" v-model="customUrl" type="url" autocomplete="off" :disabled="saving" />
        </template>
        <p v-if="reading" role="status">正在读取当前接口…</p>
        <template v-if="snapshot">
          <dl><dt>当前接口</dt><dd>{{ snapshot.api_base_url }}</dd><dt>新接口</dt><dd>{{ nextUrl || '待填写' }}</dd></dl>
          <p v-if="unchanged" role="status">此来源已使用该接口。</p>
          <OnlineCatalogueChoice v-if="nextUrl && !unchanged && !sameOperator" id="assistant-catalogue-change" v-model="catalogueChoice" :disabled="saving" />
          <p v-else-if="nextUrl && !unchanged" class="hint">已识别为同一运营方模板的换址，将沿用已有收藏、进度和缓存。</p>
          <p v-if="pending" role="status">正在预览主机变更…</p>
          <p v-if="previewError" role="alert">{{ previewError }} <JzButton size="compact" @click="retry">重新预览</JzButton></p>
          <OnlineHostChanges v-if="report?.changes.length" v-model="selected" :changes="report.changes" :disabled="saving" />
          <p v-else-if="report" class="hint">此次只更新接口地址，允许列表没有待替换的主机。</p>
        </template>
        <p v-if="reviewError" role="alert">{{ reviewError }} <JzButton size="compact" @click="readSource">重新核对</JzButton></p>
        <p v-if="receiptHint" role="alert">{{ receiptHint }}</p>
        <p v-if="access?.blocked" role="status">{{ access.blocked }}</p>
      </template>
      <p v-else>请先在“在线视频源”添加 MacCMS 来源。</p>
      <div class="assistant-actions">
        <JzButton v-if="sources.length" variant="primary" :disabled="!canApply" :loading="saving" @click="apply">确认应用</JzButton>
        <JzButton variant="ghost" :disabled="saving" @click="cancel">取消</JzButton>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { api } from '../api.js'
import { catalogueReceiptHint, sourceMatchesTemplate, useOnlineHostMigration } from '../useOnlineHostMigration.js'
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'
import OnlineHostChanges from './OnlineHostChanges.vue'
import OnlineCatalogueChoice from './OnlineCatalogueChoice.vue'

const props = defineProps({ suggestion: { type: Object, required: true }, access: Object })
const emit = defineEmits(['busy'])
const sources = ref([]), templates = ref([]), sourceId = ref(''), templateId = ref('')
const customUrl = ref(props.suggestion.api_base_url || ''), choseDestination = ref(false)
const snapshot = ref(null), finished = ref(''), reviewError = ref(''), listError = ref('')
const loading = ref(false), reading = ref(false), saving = ref(false)
let listRequest, sourceRequest, automaticSource = '', disposed = false
const nextUrl = computed(() => templateId.value
  ? templates.value.find(item => item.id === templateId.value)?.api_base_url || '' : customUrl.value.trim())
const unchanged = computed(() => nextUrl.value && nextUrl.value === snapshot.value?.api_base_url)
const catalogueChoice = ref(''), receiptHint = ref('')
const sameOperator = computed(() => snapshot.value && templates.value.some(template =>
  sourceMatchesTemplate(snapshot.value, template) && sourceMatchesTemplate({ api_base_url: nextUrl.value }, template)))
watch([sourceId, nextUrl, snapshot], () => { catalogueChoice.value = ''; receiptHint.value = '' }, { flush: 'sync' })
const baseline = computed(() => nextUrl.value && !unchanged.value && !reviewError.value && !finished.value
  ? snapshot.value?.configuration_fingerprint || '' : '')
const form = computed(() => ({ api_base_url: nextUrl.value, hosts: snapshot.value?.allowed_hosts.join('\n') || '' }))
function conflict() { reviewError.value = '来源配置已变化，请重新核对当前接口和域名。' }
const { report, selected, pending, error: previewError, retry } =
  useOnlineHostMigration({ api, sourceId, baseline, form, onConflict: conflict })
const canApply = computed(() => !!report.value && (sameOperator.value || !!catalogueChoice.value) && !props.access?.blocked && !saving.value && !finished.value)

function operatorFor(source) {
  return templates.value.find(item => sourceMatchesTemplate(source, item))
}
function sourceLabel(source) {
  const name = source.name + ' · ' + source.api_base_url
  return sources.value.some(item => item.id !== source.id && item.name === source.name && item.api_base_url === source.api_base_url)
    ? name + ' · ' + source.id : name
}
async function load() {
  listRequest?.abort()
  const request = listRequest = new AbortController()
  loading.value = true; listError.value = ''
  try {
    const [list, catalogue] = await Promise.all(['/api/online/sources', '/api/online/source-templates']
      .map(path => api(path, { signal: request.signal })))
    if (request.signal.aborted) return
    sources.value = list.items.filter(item => item.provider === 'maccms')
    templates.value = catalogue.items.filter(item => item.provider === 'maccms')
    const hint = props.suggestion.template_id
    if (hint && !customUrl.value) templateId.value = templates.value.find(item => item.id === hint)?.id || ''
    const matches = hint ? sources.value.filter(item => operatorFor(item)?.id === hint) : sources.value
    if (matches.length === 1 && !sources.value.some(item => item.id !== matches[0].id && item.name === matches[0].name)) {
      automaticSource = hint ? matches[0].id : ''
      sourceId.value = matches[0].id
    }
  } catch {
    if (!request.signal.aborted) listError.value = '无法读取视频源，请确认管理权限和连接。'
  } finally { if (!request.signal.aborted) loading.value = false }
}
async function readSource() {
  sourceRequest?.abort()
  snapshot.value = null; reviewError.value = ''
  if (!sourceId.value) { reading.value = false; return }
  const id = sourceId.value, request = sourceRequest = new AbortController()
  reading.value = true
  try {
    const source = await api('/api/online/sources/' + id, { signal: request.signal })
    if (request.signal.aborted) return
    if (source?.id !== id || source.provider !== 'maccms' || !Array.isArray(source.allowed_hosts)
      || !/^[a-f0-9]{64}$/.test(source.configuration_fingerprint || '')) throw new Error()
    sources.value = sources.value.map(item => item.id === source.id ? source : item)
    const operator = operatorFor(source)
    if (automaticSource === id && operator?.id !== props.suggestion.template_id) {
      automaticSource = ''; sourceId.value = ''
      return
    }
    if (!props.suggestion.template_id && !props.suggestion.api_base_url && !choseDestination.value && !customUrl.value) {
      templateId.value = operator?.id || ''
    }
    snapshot.value = source
  } catch {
    if (!request.signal.aborted) reviewError.value = '无法读取此来源，请检查连接与管理权限。'
  } finally { if (!request.signal.aborted) reading.value = false }
}
watch(sourceId, readSource, { flush: 'sync' })
onMounted(load)
onScopeDispose(() => { disposed = true; listRequest?.abort(); sourceRequest?.abort() })

function cancel() {
  if (saving.value) return
  finished.value = '已取消换址'
  listRequest?.abort(); sourceRequest?.abort()
}
async function apply() {
  if (!canApply.value) return
  saving.value = true; props.access?.setBusy(true); emit('busy', true)
  receiptHint.value = ''
  const replacing = !sameOperator.value && catalogueChoice.value === 'replace'
  try {
    const result = await api('/api/online/sources/' + sourceId.value + (replacing ? '/replace-catalogue' : ''), { method: replacing ? 'POST' : 'PATCH', body: JSON.stringify({
      api_base_url: nextUrl.value, host_migration: {
        expected_fingerprint: report.value.expected_fingerprint, selected_old_hosts: selected.value,
      },
    }) })
    if (disposed) return
    const source = replacing ? result?.source : result
    if (!source || typeof source.api_base_url !== 'string' || !Array.isArray(source.allowed_hosts)
      || (replacing ? result.previous?.id !== sourceId.value || source.id === sourceId.value : source.id !== sourceId.value)) throw new Error()
    if (replacing) props.access?.saved(result.previous)
    finished.value = replacing ? '已创建新片库来源，旧来源已停用。' : '已更新“' + source.name + '”的接口。可在在线视频源中测试目录。'
    props.access?.saved(source)
  } catch (failure) {
    if (disposed) return
    const hint = replacing && catalogueReceiptHint(failure)
    if (failure.status === 409) conflict()
    else if (hint) receiptHint.value = hint
    else reviewError.value = '保存结果尚未确认，请重新读取当前配置。'
  } finally {
    saving.value = false; props.access?.setBusy(false); emit('busy', false)
  }
}
</script>
