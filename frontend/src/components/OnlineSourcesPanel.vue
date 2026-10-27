<template>
  <div class="online-source-panel">
    <EmptyState v-if="loading" state="loading" title="正在加载在线视频源设置" />
    <EmptyState v-else-if="loadError" state="error" title="在线视频源设置加载失败" :text="loadError" retry @retry="load" />
    <template v-else>
      <section class="card-block">
        <div class="section-heading"><h3>在线点播</h3><JzButton :variant="settings.enabled ? 'secondary' : 'primary'" :loading="toggling" :disabled="saving || networkSaving" @click="toggleEnabled">{{ settings.enabled ? '关闭在线点播' : '启用在线点播' }}</JzButton></div>
        <p class="hint">{{ settings.enabled ? '已启用；仅已启用的来源可被查询或播放。' : '默认关闭；启用后仍需启用至少一个来源。' }} 作用于此服务的所有播放设备。</p>
        <p class="hint">优先选择免费且可直连的来源。视频由播放设备解码；此功能不会自动启动 NAS 转码或生成预览。</p>
        <p v-if="message" class="online-source-feedback" :class="{ 'is-error': messageError }" :role="messageError ? 'alert' : 'status'">{{ message }}</p>
      </section>
      <section class="card-block">
        <div class="section-heading"><h3>视频源</h3><JzButton icon="plus" :disabled="saving || networkSaving || dirty" @click="editSource()">添加来源</JzButton></div>
        <p class="hint">来源配置与本地媒体库分开。保存配置不会自动搜索或验证上游；允许的主机需包含 API 和媒体实际使用的精确域名。</p>
        <EmptyState v-if="!sources.length && !editorOpen" state="empty" title="还没有视频源" text="添加自己的 MacCMS JSON 来源，或选择开放样例目录。" />
        <div class="online-source-list"><article v-for="source in sources" :key="source.id" class="online-source-row" :data-online-source-row="source.id"><div><h4>{{ source.name }}</h4><p>{{ source.provider === 'open_catalog' ? '开放样例目录' : 'MacCMS JSON' }} · {{ source.enabled ? '已启用' : '已停用' }} · 优先级 {{ source.priority }}</p></div><div class="online-source-actions"><JzButton icon="edit" :aria-label="'编辑 ' + source.name" :disabled="saving || networkSaving || dirty" @click="editSource(source)">编辑</JzButton><JzButton icon="link" :aria-label="'网络 ' + source.name" :disabled="saving || networkSaving || dirty" @click="editNetwork(source)">网络</JzButton></div></article></div>
        <form v-if="editorOpen" class="online-source-editor" data-online-source-form @submit.prevent="saveSource">
          <h4>{{ editingId ? '编辑来源' : '添加来源' }}</h4>
          <div class="online-source-fields">
            <JzField id="online-source-name" label="来源名称"><template #default="field"><input :id="field.id" v-model="form.name" maxlength="128" required :disabled="saving" /></template></JzField>
            <JzField id="online-source-provider" label="提供方"><template #default="field"><select :id="field.id" v-model="form.provider" :disabled="saving || !!editingId" @change="providerChanged"><option value="maccms">MacCMS JSON</option><option value="open_catalog">开放样例目录</option></select></template></JzField>
            <JzField id="online-source-api" class="wide" label="接口地址" :hint="form.provider === 'open_catalog' ? '固定开放目录，不需要填入播放链接。' : 'HTTP(S) JSON 接口，不含账号、密码或查询参数。'"><template #default="field"><input :id="field.id" v-model="form.api_base_url" type="url" maxlength="2048" required :disabled="saving || form.provider === 'open_catalog'" :aria-describedby="field.describedby" placeholder="https://example.com/api.php/provide/vod/" /></template></JzField>
            <JzField id="online-source-hosts" class="wide" label="允许访问的主机" hint="每行一个精确域名或公网 IP，不填写协议、路径、端口或通配符。API 主机会自动包含。"><template #default="field"><textarea :id="field.id" v-model="form.hosts" rows="3" :disabled="saving || form.provider === 'open_catalog'" :aria-describedby="field.describedby" placeholder="media.example.com" /></template></JzField>
            <JzField id="online-source-priority" label="来源优先级" hint="数值越小越靠前；不代表已验证可直连。"><template #default="field"><input :id="field.id" v-model.number="form.priority" type="number" min="0" max="1000" required :disabled="saving" :aria-describedby="field.describedby" /></template></JzField>
            <label class="online-source-check" for="online-source-enabled"><input id="online-source-enabled" v-model="form.enabled" type="checkbox" :disabled="saving" />启用此来源</label>
          </div>
          <details v-if="form.provider === 'maccms'"><summary>接口兼容设置</summary><div class="online-source-fields"><JzField id="online-search-action" label="搜索 action"><template #default="field"><select :id="field.id" v-model="form.search_action" :disabled="saving"><option value="list">list（标准）</option><option value="detail">detail</option><option value="videolist">videolist</option></select></template></JzField><JzField id="online-detail-action" label="详情 action"><template #default="field"><select :id="field.id" v-model="form.detail_action" :disabled="saving"><option value="detail">detail（标准）</option><option value="videolist">videolist</option></select></template></JzField></div></details>
          <p v-else class="hint">开放样例仅包含 Big Buck Bunny。详情会显示 Blender Foundation、原项目和 CC BY 3.0 许可；需要显式启用来源。</p>
          <p v-if="formError" class="online-source-feedback is-error" role="alert">{{ formError }}</p>
          <div class="bar"><JzButton type="submit" variant="primary" :loading="saving" :disabled="!form.name.trim() || !form.api_base_url.trim()">保存来源</JzButton><JzButton :disabled="saving" @click="cancelEditor">取消</JzButton></div>
        </form>
      </section>
      <section class="card-block">
        <h3>来源网络</h3>
        <p class="hint">应用默认：{{ defaultSummary }}。只有“继承”才使用上一级规则；直连不会读取环境代理，也不会在失败后自动改走代理。</p>
        <p class="hint">分别配置来源默认与用途。代理档案复用已配置的网络档案；未配置代理档案时可继续使用直连。</p>
        <p v-if="networkError" class="online-source-feedback is-error" role="alert">{{ networkError }}</p>
        <form v-if="networkSource" class="online-source-editor" data-online-network-form @submit.prevent="saveNetwork">
          <h4>{{ networkSource.name }} 的网络规则</h4>
          <div class="online-network-fields"><JzField v-for="purpose in PURPOSES" :id="'online-network-' + purpose.key" :key="purpose.key" :label="purpose.label"><template #default="field"><select :id="field.id" v-model="networkChoices[purpose.key]" :disabled="networkSaving"><option value="inherit">继承{{ purpose.key === 'default' ? '应用默认' : '来源默认' }}</option><option value="direct">直连</option><option v-for="profile in profiles" :key="profile.id" :value="'profile:' + profile.id">{{ profile.name }} · {{ profile.mode === 'proxy' ? '代理' : '直连' }}</option></select></template></JzField></div>
          <p class="hint">保存规则不做连通性验证。API 与媒体可选择不同出口，图片、字幕和订阅的配置也会分别保留。</p>
          <div class="bar"><JzButton type="submit" variant="primary" :loading="networkSaving">保存网络规则</JzButton><JzButton :disabled="networkSaving" @click="cancelNetwork">取消</JzButton></div>
        </form>
        <p v-else class="hint">在上方视频源旁选择“网络”以修改其规则。</p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { OPEN_CATALOG_BASE, choiceNetworkRule, networkRuleChoice } from '../onlineCatalog.js'
import { useSettingsDraft } from '../settingsDrafts.js'
import EmptyState from './EmptyState.vue'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import '../styles/online.css'

const PURPOSES = [{ key: 'default', label: '来源默认' }, { key: 'api', label: 'API 查询' }, { key: 'media', label: '媒体取流' }, { key: 'image', label: '图片' }, { key: 'subtitle', label: '字幕' }, { key: 'subscription', label: '订阅' }]
const emptyForm = () => ({ name: '', provider: 'maccms', api_base_url: '', hosts: '', enabled: false, priority: 100, search_action: 'list', detail_action: 'detail' })
const loading = ref(true), loadError = ref(''), settings = ref({ enabled: false }), sources = ref([]), profiles = ref([]), applicationDefault = ref(null)
const toggling = ref(false), saving = ref(false), networkSaving = ref(false), message = ref(''), messageError = ref(false)
const editorOpen = ref(false), editingId = ref(''), form = ref(emptyForm()), formError = ref(''), savedForm = ref('')
const networkSource = ref(null), networkChoices = ref({}), savedNetwork = ref(''), networkError = ref('')
let disposed = false, generation = 0, networkGeneration = 0
const controllers = new Set()
const defaultSummary = computed(() => {
  const rule = applicationDefault.value?.rule
  if (!rule) return '暂不可读取'
  const profile = profiles.value.find(value => value.id === rule.profile_id)
  return profile ? `${profile.name} · ${rule.mode === 'proxy' ? '代理' : '直连'}` : rule.mode === 'direct' ? '直连' : '代理档案'
})
const dirty = computed(() => editorOpen.value && savedForm.value !== JSON.stringify(form.value)
  || !!networkSource.value && savedNetwork.value !== JSON.stringify(networkChoices.value))
useSettingsDraft({ section: 'sec-online', label: '在线视频源', dirty: () => dirty.value,
  busy: () => saving.value || toggling.value || networkSaving.value,
  discard: () => { cancelEditor(); cancelNetwork() } })
async function request(path, options = {}) {
  const controller = new AbortController(); controllers.add(controller)
  try { return await api(path, { ...options, signal: controller.signal }) }
  finally { controllers.delete(controller) }
}
async function load() {
  const turn = ++generation
  loading.value = true; loadError.value = ''; networkError.value = ''
  try {
    const [state, response] = await Promise.all([request('/api/online/settings'), request('/api/online/sources')])
    if (disposed || turn !== generation) return
    settings.value = state; sources.value = response.items || []
    try {
      const [networkProfiles, appDefault] = await Promise.all([request('/api/online/network/profiles'), request('/api/online/network/default')])
      if (disposed || turn !== generation) return
      profiles.value = networkProfiles.items || []; applicationDefault.value = appDefault
    } catch { if (!disposed && turn === generation) networkError.value = '网络档案暂不可读取，请重新加载后再配置网络。' }
  } catch { if (!disposed && turn === generation) loadError.value = '无法读取在线视频源设置，请检查连接与管理权限。' }
  finally { if (!disposed && turn === generation) loading.value = false }
}
function feedback(text, error = false) { message.value = text; messageError.value = error }
async function toggleEnabled() {
  toggling.value = true; feedback('')
  try {
    const result = await request('/api/online/settings', { method: 'PATCH', body: JSON.stringify({ enabled: !settings.value.enabled }) })
    if (disposed) return
    settings.value = result; feedback(result.enabled ? '已启用在线点播。' : '已关闭在线点播，活动在线会话已撤销。')
  } catch { if (!disposed) feedback('保存失败，请重新加载核对在线点播状态。', true) }
  finally { if (!disposed) toggling.value = false }
}
function editSource(source = null) {
  if (saving.value || networkSaving.value) return
  networkGeneration++; cancelNetwork()
  editingId.value = source?.id || ''; formError.value = ''
  form.value = source ? { name: source.name, provider: source.provider, api_base_url: source.api_base_url,
    hosts: source.allowed_hosts.join('\n'), enabled: source.enabled, priority: source.priority,
    search_action: source.provider_options?.search_action || 'list', detail_action: source.provider_options?.detail_action || 'detail' } : emptyForm()
  savedForm.value = JSON.stringify(form.value); editorOpen.value = true
}
function providerChanged() {
  if (form.value.provider === 'open_catalog') {
    form.value.api_base_url = OPEN_CATALOG_BASE
    form.value.hosts = 'www.bigbuckbunny.org\nvideo.blender.org'
    if (!form.value.name.trim()) form.value.name = '开放样例目录'
  } else { form.value.api_base_url = ''; form.value.hosts = '' }
}
function cancelEditor() { editorOpen.value = false; editingId.value = ''; formError.value = ''; form.value = emptyForm(); savedForm.value = '' }
async function saveSource() {
  if (saving.value) return
  saving.value = true; formError.value = ''
  const id = editingId.value
  try {
    const values = { name: form.value.name.trim(), api_base_url: form.value.api_base_url.trim(),
      allowed_hosts: [...new Set(form.value.hosts.split(/[\s,]+/).filter(Boolean))], enabled: form.value.enabled,
      priority: form.value.priority, provider_options: { search_action: form.value.search_action, detail_action: form.value.detail_action } }
    if (!id) values.provider = form.value.provider
    const source = await request('/api/online/sources' + (id ? '/' + id : ''), { method: id ? 'PATCH' : 'POST', body: JSON.stringify(values) })
    if (disposed) return
    sources.value = [...sources.value.filter(value => value.id !== source.id), source].sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name))
    cancelEditor(); feedback('来源已保存；尚未验证上游连通性。')
  } catch { if (!disposed) formError.value = '保存失败，请检查名称、接口、允许主机和字段格式；若请求超时，请刷新后核对是否已经保存。' }
  finally { if (!disposed) saving.value = false }
}
async function editNetwork(source) {
  if (saving.value || networkSaving.value) return
  cancelEditor(); const turn = ++networkGeneration
  networkSource.value = null; networkError.value = ''
  try {
    const response = await request('/api/online/sources/' + source.id + '/network')
    if (disposed || turn !== networkGeneration) return
    networkChoices.value = Object.fromEntries(PURPOSES.map(purpose => [purpose.key, networkRuleChoice(response.policy[purpose.key])]))
    savedNetwork.value = JSON.stringify(networkChoices.value); networkSource.value = source
  } catch { if (!disposed && turn === networkGeneration) networkError.value = '此来源的网络规则暂不可读取，请重试。' }
}
function cancelNetwork() { networkGeneration++; networkSource.value = null; networkChoices.value = {}; savedNetwork.value = '' }
async function saveNetwork() {
  if (networkSaving.value || !networkSource.value) return
  networkSaving.value = true; networkError.value = ''
  try {
    const rules = Object.fromEntries(PURPOSES.map(purpose => [purpose.key, choiceNetworkRule(networkChoices.value[purpose.key], profiles.value)]))
    await request('/api/online/sources/' + networkSource.value.id + '/network', { method: 'PATCH', body: JSON.stringify(rules) })
    if (disposed) return
    cancelNetwork(); feedback('网络规则已保存；后续请求按各用途的新规则建立连接。')
  } catch { if (!disposed) networkError.value = '网络规则保存失败，请检查档案是否有效，刷新后核对。' }
  finally { if (!disposed) networkSaving.value = false }
}
onMounted(load)
onUnmounted(() => { disposed = true; generation++; networkGeneration++; controllers.forEach(controller => controller.abort()); controllers.clear() })
</script>
