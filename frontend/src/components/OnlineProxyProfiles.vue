<template>
  <section id="online-proxy-configurations" class="card-block">
    <h3>代理配置</h3>
    <p class="hint">配置保存在此服务，可供多个来源共用。名称只是备注；请核对实际地址。编辑会影响所有引用此配置的来源。</p>
    <p v-if="message" class="online-source-feedback" :class="{ 'is-error': failed }" :role="failed ? 'alert' : 'status'">{{ message }}</p>
    <form class="online-default-network" data-online-default-form @submit.prevent="saveDefault">
      <JzField id="online-service-default" label="服务默认连接" hint="只影响沿用服务默认的来源；来源及用途的单独规则优先。">
        <template #default="field"><select :id="field.id" v-model="defaultChoice" :disabled="disabled || busy || !applicationDefault" :aria-describedby="field.describedby"><option value="direct">直连</option><option v-for="profile in profiles" :key="profile.id" :value="'profile:' + profile.id">{{ profile.name }} · {{ profile.mode === 'proxy' ? profile.proxy_url : '直连' }}</option></select></template>
        <template #actions><JzButton type="submit" :loading="busy === 'default'" :disabled="disabled || !!busy || !defaultDirty || !applicationDefault">保存服务默认</JzButton><JzButton v-if="defaultDirty" :disabled="!!busy" @click="resetDefault">取消</JzButton></template>
      </JzField>
    </form>
    <p v-if="!proxyProfiles.length" class="hint">还没有保存的代理。在来源的“网络”中填写新的代理后，可在这里查看和管理。</p>
    <div class="online-proxy-list">
      <article v-for="profile in proxyProfiles" :key="profile.id" class="online-proxy-row" :data-online-proxy-row="profile.id">
        <div class="online-proxy-description">
          <h4>{{ profile.name }}</h4>
          <p class="online-proxy-endpoint">{{ profile.proxy_url }} · {{ profile.has_credentials ? '已保存认证信息' : '未配置认证' }}</p>
          <details><summary>连接参数</summary><dl class="online-proxy-details"><dt>连接超时</dt><dd>{{ profile.connect_timeout_seconds }} 秒</dd><dt>读取超时</dt><dd>{{ profile.read_timeout_seconds }} 秒</dd><dt>最大连接数</dt><dd>{{ profile.max_connections }}</dd><dt>CA 证书文件</dt><dd>{{ profile.ca_file || '服务默认' }}</dd><dt>CA 证书目录</dt><dd>{{ profile.ca_dir || '服务默认' }}</dd></dl></details>
        </div>
        <div class="online-source-actions"><JzButton icon="edit" :aria-label="'编辑代理 ' + profile.name" :disabled="disabled || !!busy || dirty" @click="edit(profile)">编辑</JzButton><JzButton icon="trash" :aria-label="'删除代理 ' + profile.name" :disabled="disabled || !!busy || dirty" @click="pendingDelete = profile.id; message = ''">删除</JzButton></div>
        <div v-if="pendingDelete === profile.id" class="online-proxy-delete" role="group" :aria-label="'确认删除代理 ' + profile.name"><p>删除“{{ profile.name }}”？正在被规则使用的配置会保留，请先修改相关规则。</p><JzButton variant="danger" :loading="busy === profile.id" :disabled="disabled || !!busy" @click="remove(profile)">确认删除</JzButton><JzButton :disabled="!!busy" @click="pendingDelete = ''">取消删除</JzButton></div>
      </article>
    </div>
    <form v-if="editingId" class="online-source-editor" data-online-proxy-form @submit.prevent="saveProfile">
      <h4>编辑代理配置</h4>
      <JzField id="online-profile-name" label="配置名称"><template #default="field"><input :id="field.id" v-model="form.name" maxlength="128" required :disabled="disabled || !!busy" /></template></JzField>
      <div class="online-proxy-fields">
        <JzField id="online-profile-protocol" label="代理协议"><template #default="field"><select :id="field.id" v-model="form.protocol" :disabled="disabled || !!busy"><option value="http">HTTP</option><option value="https">HTTPS</option></select></template></JzField>
        <JzField id="online-profile-host" label="代理服务器地址" hint="从 jzmedia 服务所在网络访问；127.0.0.1 在 bridge 容器中指容器自身，host 网络下与宿主机共用。"><template #default="field"><input :id="field.id" v-model="form.host" required :disabled="disabled || !!busy" autocomplete="off" :spellcheck="false" :aria-describedby="field.describedby" /></template></JzField>
        <JzField id="online-profile-port" label="端口"><template #default="field"><input :id="field.id" v-model="form.port" required type="number" min="1" max="65535" inputmode="numeric" :disabled="disabled || !!busy" /></template></JzField>
      </div>
      <details class="online-proxy-auth"><summary>修改代理认证（可选）</summary><p class="hint">已保存的用户名和密码不会回显。留空保留对应的原值，填写则替换；清除认证需勾选下方选项。</p><div class="online-proxy-credentials">
        <JzField id="online-profile-username" label="新用户名"><template #default="field"><input :id="field.id" v-model="form.username" :disabled="disabled || !!busy || form.clear_credentials" autocomplete="off" /></template></JzField>
        <JzField id="online-profile-password" label="新密码"><template #default="field"><input :id="field.id" v-model="form.password" type="password" :disabled="disabled || !!busy || form.clear_credentials" autocomplete="new-password" /></template></JzField>
      </div><label class="online-source-check" for="online-profile-clear"><input id="online-profile-clear" v-model="form.clear_credentials" type="checkbox" :disabled="disabled || !!busy" @change="form.username = ''; form.password = ''" />清除已保存的认证信息</label></details>
      <p class="hint">保存后请关闭旧播放器再重新打开，并使用“测试来源”检查目录。代理不可用时不会自动回退直连。</p>
      <div class="bar"><JzButton type="submit" variant="primary" :loading="busy === 'profile'" :disabled="disabled || !!busy">保存代理配置</JzButton><JzButton :disabled="!!busy" @click="cancelEdit">取消编辑</JzButton></div>
    </form>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { choiceNetworkRule, networkRuleChoice } from '../onlineCatalog.js'
import { proxyProfileForm, proxyProfilePatchPayload } from '../onlineSourceNetwork.js'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'

const props = defineProps({ profiles: { type: Array, default: () => [] }, applicationDefault: { type: Object, default: null }, disabled: Boolean })
const emit = defineEmits(['updated', 'deleted', 'defaultUpdated', 'busy', 'dirty'])
const editingId = ref(''), form = ref({}), savedForm = ref(''), pendingDelete = ref('')
const defaultChoice = ref('direct'), busy = ref(''), message = ref(''), failed = ref(false)
const proxyProfiles = computed(() => props.profiles.filter(profile => profile.mode === 'proxy'))
const savedDefault = computed(() => props.applicationDefault ? networkRuleChoice(props.applicationDefault.rule) : 'direct')
const defaultDirty = computed(() => defaultChoice.value !== savedDefault.value)
const dirty = computed(() => defaultDirty.value || !!editingId.value && savedForm.value !== JSON.stringify(form.value))
const controllers = new Set()
let disposed = false
watch(savedDefault, value => { defaultChoice.value = value }, { immediate: true })
watch(dirty, value => emit('dirty', value), { flush: 'sync' })
watch(busy, value => emit('busy', !!value), { flush: 'sync' })
async function request(path, options) {
  const controller = new AbortController(); controllers.add(controller)
  try { return await api(path, { ...options, signal: controller.signal }) }
  finally { controllers.delete(controller) }
}
function feedback(text, error = false) { message.value = text; failed.value = error }
function cancelEdit() { editingId.value = ''; form.value = {}; savedForm.value = '' }
function resetDefault() { defaultChoice.value = savedDefault.value }
function discard() { cancelEdit(); resetDefault(); pendingDelete.value = ''; feedback('') }
function edit(profile) {
  if (props.disabled || busy.value || dirty.value) return
  form.value = proxyProfileForm(profile); savedForm.value = JSON.stringify(form.value)
  editingId.value = profile.id; pendingDelete.value = ''; feedback('')
}
async function saveProfile() {
  if (props.disabled || busy.value || !editingId.value) return
  let payload
  try { payload = proxyProfilePatchPayload(form.value) }
  catch (error) { feedback(error.message, true); return }
  busy.value = 'profile'; feedback('')
  try {
    const profile = await request('/api/online/network/profiles/' + editingId.value, { method: 'PATCH', body: JSON.stringify(payload) })
    if (disposed) return
    emit('updated', profile); cancelEdit(); feedback('代理配置已更新；请重新打开播放器以使用新配置。')
  } catch { if (!disposed) feedback('代理配置保存失败，请检查地址及管理权限后重试。', true) }
  finally { if (!disposed) busy.value = '' }
}
async function saveDefault() {
  if (props.disabled || busy.value || !defaultDirty.value || !props.applicationDefault) return
  busy.value = 'default'; feedback('')
  try {
    const result = await request('/api/online/network/default', { method: 'PATCH', body: JSON.stringify({ rule: choiceNetworkRule(defaultChoice.value, props.profiles) }) })
    if (disposed) return
    emit('defaultUpdated', result); feedback('服务默认连接已保存；来源与用途的单独规则继续保留。')
  } catch { if (!disposed) feedback('服务默认连接保存失败，请重新加载后核对。', true) }
  finally { if (!disposed) busy.value = '' }
}
async function remove(profile) {
  if (props.disabled || busy.value || dirty.value) return
  busy.value = profile.id; feedback('')
  try {
    await request('/api/online/network/profiles/' + profile.id, { method: 'DELETE' })
    if (disposed) return
    if (editingId.value === profile.id) cancelEdit()
    emit('deleted', profile.id); pendingDelete.value = ''; feedback('代理配置已删除。')
  } catch (error) {
    if (!disposed) feedback(error.status === 409 ? '此代理仍被来源网络规则或服务默认使用。请先将相关连接改为直连或其它代理，保存后再删除。' : '代理配置删除失败，请重新加载后核对。', true)
  } finally { if (!disposed) busy.value = '' }
}
onUnmounted(() => { disposed = true; controllers.forEach(controller => controller.abort()); controllers.clear() })
defineExpose({ discard })
</script>
