<template>
  <JzDialog title="允许此来源的媒体主机" :busy="saving || authorizing" :focus-active="!viewing.prompt" data-online-host-dialog @close="close">
    <div class="online-host-dialog">
      <p>《{{ title }}》的部分分集使用了尚未允许的媒体域名，检查也会列出需要的图片域名。</p>
      <p class="online-scope">确认后对本服务的“{{ sourceName }}”视频源生效，此来源正在播放的会话会关闭。只添加你勾选的域名，不改变网络代理设置。</p>
      <p v-if="checking" data-online-host-loading role="status">正在检查媒体主机…</p>
      <p v-if="permissionRequired" role="status">需要管理权限才能检查并添加媒体主机。</p>
      <p v-if="saved" data-online-host-saved role="status">媒体主机已加入允许列表。</p>
      <p v-if="error" class="online-host-dialog-error" data-online-host-error role="alert">{{ error }}</p>
      <template v-if="report && !saved">
        <p v-if="!report.missing_hosts.length" role="status">没有发现需要新增的域名，可重新加载详情。</p>
        <fieldset v-else class="online-host-dialog-hosts" :disabled="saving">
          <legend>确认允许的域名</legend>
          <label v-for="host in report.missing_hosts" :key="host"><input v-model="selected" type="checkbox" :value="host" data-online-host-candidate /><span>{{ host }}</span></label>
        </fieldset>
        <p v-if="report.rejected_count" class="online-scope">部分地址格式不受支持，未列入候选。</p>
      </template>
    </div>
    <template #footer>
      <JzButton :disabled="saving || authorizing" @click="close">暂不添加</JzButton>
      <JzButton v-if="permissionRequired" variant="primary" :loading="authorizing" :disabled="saving" @click="authorize">验证并检查</JzButton>
      <JzButton v-else-if="saved || report && !report.missing_hosts.length" variant="primary" :loading="saving" @click="reload">重新加载详情</JzButton>
      <JzButton v-else-if="report" variant="primary" :loading="saving" :disabled="!selected.length || checking" @click="apply">确认添加</JzButton>
      <JzButton v-else variant="primary" :loading="checking" :disabled="saving" @click="check">重新检查</JzButton>
    </template>
  </JzDialog>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { ensureManagement, refreshViewing, viewing } from '../viewing.js'
import { onlineHostError, validOnlineHostReport } from '../onlineHosts.js'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'

const props = defineProps({ sourceId: { type: String, required: true }, externalId: { type: String, required: true },
  title: { type: String, required: true }, sourceName: { type: String, required: true },
  current: { type: Function, required: true }, refresh: { type: Function, required: true } })
const emit = defineEmits(['close'])
const report = ref(null), selected = ref([]), checking = ref(false), saving = ref(false), error = ref(''), saved = ref(false)
const permissionRequired = ref(!!viewing.enabled && !viewing.management_unlocked), authorizing = ref(false)
let disposed = false, generation = 0, controller = null
function current(turn) { return !disposed && turn === generation && props.current() }
function close() { if (!saving.value && !authorizing.value) emit('close') }
async function request(action, body, signal) {
  return api('/api/online/sources/' + props.sourceId + '/' + action,
    { method: 'POST', body: JSON.stringify(body), signal, timeout: 35000, silentViewing: true })
}
function failed(problem) {
  if (['management_required', 'online_management_revoked'].includes(problem?.code)) permissionRequired.value = true
  error.value = onlineHostError(problem)
}
async function check() {
  if (checking.value || saving.value || authorizing.value || permissionRequired.value || !props.current()) return
  const turn = ++generation
  controller?.abort(); controller = new AbortController()
  checking.value = true; error.value = ''; report.value = null; selected.value = []
  try {
    const value = await request('host-check', { external_id: props.externalId }, controller.signal)
    if (!current(turn)) return
    if (!validOnlineHostReport(value, props.sourceId, props.externalId)) throw new Error('invalid host report')
    report.value = value; selected.value = [...value.missing_hosts]
  } catch (problem) { if (current(turn)) failed(problem) }
  finally { if (current(turn)) checking.value = false }
}
async function authorize() {
  if (authorizing.value || saving.value || !props.current()) return
  const turn = generation
  authorizing.value = true; error.value = ''
  try {
    // A management lease may have expired on the server since this page loaded.
    await refreshViewing()
    if (!current(turn)) return
    if (viewing.enabled && !viewing.management_unlocked && !await ensureManagement()) return
    if (!current(turn)) return
    permissionRequired.value = false
  } catch (problem) { if (current(turn)) failed(problem) }
  finally { if (current(turn)) authorizing.value = false }
  if (current(turn) && !permissionRequired.value) await check()
}
async function reload() {
  if (saving.value || checking.value || !props.current()) return
  const turn = generation
  saving.value = true; error.value = ''
  try { await props.refresh(); if (current(turn)) emit('close') }
  catch { if (current(turn)) error.value = onlineHostError(null, saved.value) }
  finally { if (current(turn)) saving.value = false }
}
async function apply() {
  if (saving.value || checking.value || !report.value || !selected.value.length || !props.current()) return
  const turn = generation, checked = report.value
  const hosts = selected.value.filter(host => checked.missing_hosts.includes(host))
  if (!hosts.length) return
  controller?.abort(); controller = new AbortController()
  saving.value = true; error.value = ''
  try {
    await request('allow-hosts', { hosts, expected_fingerprint: checked.configuration_fingerprint }, controller.signal)
    if (!current(turn)) return
    saved.value = true; report.value = null; selected.value = []
    await props.refresh()
    if (current(turn)) emit('close')
  } catch (problem) {
    if (current(turn)) {
      report.value = null; selected.value = []
      if (saved.value) error.value = onlineHostError(problem, true)
      else failed(problem)
    }
  } finally { if (current(turn)) saving.value = false }
}
onMounted(() => { if (!permissionRequired.value) void check() })
onUnmounted(() => { disposed = true; generation++; controller?.abort() })
</script>

<style scoped>
.online-host-dialog { display: grid; gap: var(--jz-gap-m); }
.online-host-dialog p { margin: 0; line-height: 1.7; }
.online-host-dialog-error { color: var(--jz-danger); overflow-wrap: anywhere; }
.online-host-dialog-hosts { display: grid; gap: var(--jz-gap-s); min-width: 0; padding: var(--jz-gap-m); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); }
.online-host-dialog-hosts label { display: flex; align-items: center; gap: var(--jz-gap-m); min-height: var(--jz-touch-target); cursor: pointer; }
.online-host-dialog-hosts span { min-width: 0; overflow-wrap: anywhere; }
.online-host-dialog-hosts input { flex: none; }
</style>
