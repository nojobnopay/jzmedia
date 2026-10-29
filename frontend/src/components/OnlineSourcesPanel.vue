<template>
  <div class="online-source-panel">
    <EmptyState v-if="loading" state="loading" title="正在加载在线视频源设置" />
    <EmptyState v-else-if="loadError" state="error" title="在线视频源设置加载失败" :text="loadError" retry @retry="load" />
    <template v-else>
      <section class="card-block">
        <div class="section-heading"><h3>在线点播</h3><JzButton :variant="settings.enabled ? 'secondary' : 'primary'" :loading="toggling" :disabled="saving || networkSaving || hostApplying" @click="toggleEnabled">{{ settings.enabled ? '关闭在线点播' : '启用在线点播' }}</JzButton></div>
        <p class="hint">{{ settings.enabled ? '已启用；仅已启用的来源可被查询或播放。' : '默认关闭；启用后仍需启用至少一个来源。' }} 作用于此服务的所有播放设备。</p>
        <p class="hint">优先选择免费且可直连的来源。视频由播放设备解码；此功能不会自动启动 NAS 转码或生成预览。</p>
        <p v-if="message" class="online-source-feedback" :class="{ 'is-error': messageError }" :role="messageError ? 'alert' : 'status'">{{ message }}</p>
      </section>
      <section class="card-block">
        <div class="section-heading"><h3>视频源</h3><JzButton icon="plus" :disabled="saving || networkSaving || dirty || hostApplying" @click="editSource()">添加来源</JzButton></div>
        <p class="hint">从来源目录选择后即可保存，也可以填写自己的接口。新增来源默认启用；保存后可测试目录，实际播放还取决于媒体线路。</p>
        <EmptyState v-if="!sources.length && !editorOpen" state="empty" title="还没有视频源" text="添加自己的 MacCMS JSON 来源，或选择开放样例目录。" />
        <div class="online-source-list"><article v-for="source in sources" :key="source.id" class="online-source-row" :data-online-source-row="source.id"><div><h4>{{ source.name }}</h4><p>{{ source.provider === 'open_catalog' ? '开放样例目录' : 'MacCMS JSON' }} · {{ source.enabled ? '已启用' : '已停用' }} · 优先级 {{ source.priority }}</p><p v-if="sourceChecks[source.id]" role="status">{{ sourceChecks[source.id] }}</p></div><div class="online-source-actions"><JzButton :aria-label="'测试来源 ' + source.name" :loading="testingSource === source.id" :disabled="!!testingSource || hostChecking || saving || networkSaving || hostApplying || !settings.enabled || !source.enabled" @click="testSource(source)">测试来源</JzButton><JzButton icon="edit" :aria-label="'编辑 ' + source.name" :disabled="saving || networkSaving || dirty || hostApplying" @click="editSource(source)">编辑</JzButton><JzButton icon="link" :aria-label="'网络 ' + source.name" :disabled="saving || networkSaving || dirty || hostApplying" @click="editNetwork(source)">网络</JzButton></div></article></div>
        <p v-if="sources.length && !settings.enabled" class="hint">先启用上方的在线点播，再测试已启用来源。</p>
        <form v-if="editorOpen" class="online-source-editor" data-online-source-form @submit.prevent="saveSource">
          <h4>{{ editingId ? '编辑来源' : '添加来源' }}</h4>
          <template v-if="!editingId">
            <JzField id="online-source-template" label="选择视频源" hint="选择来源会预填配置；不会自动添加其他来源或联系上游。"><template #default="field"><select :id="field.id" v-model="templateId" :disabled="saving" :aria-describedby="field.describedby" @change="applyTemplate"><option value="">自定义接口</option><option v-for="item in sourceTemplates" :key="item.id" :value="item.id">{{ item.name }}</option></select></template></JzField>
            <p v-if="templatesLoading" class="hint" role="status">正在加载来源目录…</p>
            <p v-if="templatesError" class="online-source-feedback is-error" role="alert">来源目录暂时无法读取，仍可填写自定义接口。<JzButton size="compact" :disabled="templatesLoading" @click="loadTemplates">重试目录</JzButton></p>
            <div v-if="selectedTemplate" class="online-template-description"><p>{{ selectedTemplate.description }}</p><p class="hint">{{ selectedTemplate.status === 'api_observed' ? '已有目录读取记录，播放仍需验证。' : '待验证。' }} {{ selectedTemplate.network_note }}</p><a v-if="templateHomepage" :href="templateHomepage" target="_blank" rel="noopener noreferrer">来源说明</a></div>
          </template>
          <div class="online-source-fields">
            <JzField id="online-source-name" label="来源名称"><template #default="field"><input :id="field.id" v-model="form.name" maxlength="128" required :disabled="saving" /></template></JzField>
            <JzField id="online-source-provider" label="提供方"><template #default="field"><select :id="field.id" v-model="form.provider" :disabled="saving || !!editingId" @change="providerChanged"><option value="maccms">MacCMS JSON</option><option value="open_catalog">开放样例目录</option></select></template></JzField>
            <JzField id="online-source-api" class="wide" label="接口地址" :hint="form.provider === 'open_catalog' ? '固定开放目录，不需要填入播放链接。' : 'HTTP(S) JSON 接口，不含账号、密码或查询参数。'"><template #default="field"><input :id="field.id" v-model="form.api_base_url" type="url" maxlength="2048" required :disabled="saving || form.provider === 'open_catalog'" :aria-describedby="field.describedby" placeholder="https://example.com/api.php/provide/vod/" /></template></JzField>
            <JzField id="online-source-hosts" class="wide" label="允许访问的主机" hint="每行一个精确域名或公网 IP，不填写协议、路径、端口或通配符。API 主机会自动包含。"><template #default="field"><textarea :id="field.id" v-model="form.hosts" rows="3" :disabled="saving || form.provider === 'open_catalog'" :aria-describedby="field.describedby" placeholder="media.example.com" /></template></JzField>
            <JzField id="online-source-priority" label="来源优先级" hint="数值越小越靠前；不代表已验证可直连。"><template #default="field"><input :id="field.id" v-model.number="form.priority" type="number" min="0" max="1000" required :disabled="saving" :aria-describedby="field.describedby" /></template></JzField>
            <label class="online-source-check" for="online-source-enabled"><input id="online-source-enabled" v-model="form.enabled" type="checkbox" :disabled="saving" />启用此来源</label>
          </div>
          <details v-if="form.provider === 'maccms'"><summary>接口兼容设置</summary><div class="online-source-fields"><JzField id="online-search-action" label="搜索 action"><template #default="field"><select :id="field.id" v-model="form.search_action" :disabled="saving"><option value="list">list（标准）</option><option value="detail">detail</option><option value="videolist">videolist</option></select></template></JzField><JzField id="online-detail-action" label="详情 action"><template #default="field"><select :id="field.id" v-model="form.detail_action" :disabled="saving"><option value="detail">detail（标准）</option><option value="videolist">videolist</option></select></template></JzField></div></details>
          <p v-else class="hint">开放样例用于验证播放能力。影片详情保留发布者、原项目和许可说明。</p>
          <p v-if="formError" class="online-source-feedback is-error" role="alert">{{ formError }}</p>
          <div class="bar"><JzButton type="submit" variant="primary" :loading="saving" :disabled="!form.name.trim() || !form.api_base_url.trim()">保存来源</JzButton><JzButton :disabled="saving" @click="cancelEditor">取消</JzButton></div>
        </form>
      </section>
      <section v-if="hostSource" class="card-block online-host-check" data-online-host-check>
        <div class="section-heading"><h3>{{ hostSource.name }} · 检查媒体主机</h3><JzButton :disabled="hostApplying" @click="cancelHostCheck">关闭检查</JzButton></div>
        <p class="hint">检查所选内容的目录信息，列出尚未允许的媒体和图片主机。检查不会访问这些新主机，也不会自动修改来源。</p>
        <form class="online-host-form" data-online-host-form @submit.prevent="checkHosts">
          <JzField id="online-host-item" label="来源内容编号" hint="从影片详情进入时自动填写。"><template #default="field"><input :id="field.id" v-model="hostExternalId" maxlength="128" autocomplete="off" :disabled="hostChecking || hostApplying" :aria-describedby="field.describedby" /></template></JzField>
          <JzButton type="submit" :loading="hostChecking" :disabled="hostApplying || !!testingSource || !validHostItem || !settings.enabled || !hostSource.enabled">检查媒体主机</JzButton>
        </form>
        <p v-if="!settings.enabled || !hostSource.enabled" class="hint">先启用在线点播和此来源，再检查所选内容。</p>
        <p v-if="hostError" class="online-source-feedback is-error" role="alert">{{ hostError }}</p>
        <div v-if="hostReport" class="online-host-report" aria-label="媒体主机检查结果">
          <template v-if="hostReport.missing_hosts.length">
            <p>以下主机尚未加入此来源。核对后选择要允许的主机，再保存。</p>
            <ul class="online-host-list"><li v-for="host in hostReport.missing_hosts" :key="host"><label><input v-model="selectedHosts" type="checkbox" :value="host" :disabled="hostApplying" /><span>{{ host }}</span></label></li></ul>
            <JzButton variant="primary" :loading="hostApplying" :disabled="!selectedHosts.length" @click="allowCheckedHosts">加入允许列表</JzButton>
            <p class="hint">仅新增勾选的精确主机，保留原有允许列表和其它配置。保存后会关闭此来源的旧播放会话。</p>
          </template>
          <p v-else role="status">未发现需要新增的主机，可返回影片详情重新加载线路。</p>
          <p v-if="hostReport.rejected_count" class="hint">{{ hostReport.rejected_count }} 个地址格式或主机不符合要求，未列为可添加项。</p>
        </div>
        <p v-if="hostApplied" class="online-source-feedback" role="status">媒体主机已加入允许列表。返回影片详情后会重新读取播放线路。</p>
        <router-link v-if="validHostItem" :to="hostReturnTarget" data-online-host-return>返回影片详情</router-link>
      </section>
      <section class="card-block">
        <h3>来源网络</h3>
        <p class="hint">按运行 jzmedia 的服务器能否访问来源，选择直连或代理。服务默认：{{ defaultSummary }}。</p>
        <p v-if="networkError" class="online-source-feedback is-error" role="alert">{{ networkError }}</p>
        <form v-if="networkSource" class="online-source-editor" data-online-network-form @submit.prevent="saveNetwork">
          <h4>{{ networkSource.name }} 的网络规则</h4>
          <JzField id="online-network-mode" class="online-connection-choice" label="连接方式" :hint="networkChoices.default === 'inherit' ? '当前沿用服务默认。切换后将为此来源单独保存。' : '此连接用于本来源；高级设置中的单独规则会保留。'"><template #default="field"><select :id="field.id" :value="networkMode" :disabled="networkSaving" :aria-describedby="field.describedby" @change="changeNetworkMode($event.target.value)"><option v-if="networkMode === 'unavailable'" value="unavailable" disabled>默认网络暂不可用</option><option value="direct">直连</option><option value="proxy">HTTP(S) 代理</option></select></template></JzField>
          <template v-if="networkMode === 'proxy'">
            <JzField id="online-proxy-profile" class="online-connection-choice" label="代理配置"><template #default="field"><select :id="field.id" :value="proxyChoice" :disabled="networkSaving" @change="selectProxy($event.target.value)"><option value="new">填写新的代理</option><option v-for="profile in proxyProfiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option></select></template></JzField>
            <div v-if="proxyChoice === 'new'" class="online-proxy-fields">
              <JzField id="online-proxy-protocol" label="代理协议"><template #default="field"><select :id="field.id" v-model="proxyForm.protocol" :disabled="networkSaving"><option value="http">HTTP</option><option value="https">HTTPS</option></select></template></JzField>
              <JzField id="online-proxy-host" label="代理服务器地址" hint="填写 jzmedia 服务器能访问的主机名或 IP，不是浏览器所在设备的地址。"><template #default="field"><input :id="field.id" v-model="proxyForm.host" :disabled="networkSaving" autocomplete="off" :spellcheck="false" :aria-describedby="field.describedby" placeholder="例如 192.168.1.10" /></template></JzField>
              <JzField id="online-proxy-port" label="端口"><template #default="field"><input :id="field.id" v-model="proxyForm.port" :disabled="networkSaving" type="number" min="1" max="65535" inputmode="numeric" placeholder="7890" /></template></JzField>
              <details class="online-proxy-auth"><summary>代理认证（可选）</summary><div class="online-proxy-credentials"><JzField id="online-proxy-username" label="代理用户名"><template #default="field"><input :id="field.id" v-model="proxyForm.username" :disabled="networkSaving" autocomplete="off" /></template></JzField><JzField id="online-proxy-password" label="代理密码" hint="仅写入保存，不会回显。"><template #default="field"><input :id="field.id" v-model="proxyForm.password" :disabled="networkSaving" type="password" autocomplete="new-password" :aria-describedby="field.describedby" /></template></JzField></div></details>
            </div>
            <p v-else class="hint online-proxy-endpoint">{{ selectedProxy?.proxy_url }}{{ selectedProxy?.has_credentials ? ' · 已保存认证信息' : '' }}。现有代理可供多个来源共用；需要不同地址时选择“填写新的代理”。</p>
          </template>
          <div class="online-network-paths" aria-label="当前网络路径"><p>搜索与详情：{{ purposeSummary('api') }}</p><p>视频播放：{{ purposeSummary('media') }}</p></div>
          <p v-if="separateRules" class="online-network-overrides">已保留 {{ separateRules }} 项单独网络规则，修改上方连接不会覆盖它们。<JzButton size="compact" :disabled="networkSaving" @click="inheritAllPurposes">全部用途使用此连接</JzButton></p>
          <details class="online-network-advanced"><summary>高级：按用途分别配置</summary>
            <p class="hint">只有搜索接口、视频或图片需要不同出口时才分别设置。“继承来源默认”跟随上方连接；“直连”始终绕过代理，不随默认设置改变。</p>
            <div class="online-network-fields"><JzField v-for="purpose in PURPOSES" :id="'online-network-' + purpose.key" :key="purpose.key" :label="purpose.label"><template #default="field"><select :id="field.id" v-model="networkChoices[purpose.key]" :disabled="networkSaving"><option value="inherit">继承{{ purpose.key === 'default' ? '服务默认' : '来源默认' }}</option><option value="direct">直连</option><option v-if="networkChoices[purpose.key] === 'new-proxy'" value="new-proxy">新代理（待保存）</option><option v-for="profile in profiles" :key="profile.id" :value="'profile:' + profile.id">{{ profile.name }} · {{ profile.mode === 'proxy' ? '代理' : '直连' }}</option></select></template></JzField></div>
          </details>
          <p class="hint">保存规则不做连通性验证。API 与媒体可选择不同出口，图片、字幕和订阅的配置也会分别保留。</p>
          <div class="bar"><JzButton type="submit" variant="primary" :loading="networkSaving">保存网络规则</JzButton><JzButton :disabled="networkSaving" @click="cancelNetwork">取消</JzButton></div>
        </form>
        <p v-else class="hint">在上方视频源旁选择“网络”以修改其规则。</p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api.js'
import { OPEN_CATALOG_BASE, choiceNetworkRule, networkRuleChoice, onlineCatalogError, onlineRouteTarget } from '../onlineCatalog.js'
import { SOURCE_NETWORK_PURPOSES, emptyOnlineSourceForm, resolveSourceNetworkChoice,
  networkChoiceSummary, proxyProfilePayload } from '../onlineSourceNetwork.js'
import { useSettingsDraft } from '../settingsDrafts.js'
import EmptyState from './EmptyState.vue'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import '../styles/online.css'
import '../styles/online-source.css'

const PURPOSES = SOURCE_NETWORK_PURPOSES
const emptyForm = emptyOnlineSourceForm
const loading = ref(true), loadError = ref(''), settings = ref({ enabled: false }), sources = ref([]), profiles = ref([]), applicationDefault = ref(null)
const toggling = ref(false), saving = ref(false), networkSaving = ref(false), message = ref(''), messageError = ref(false)
const editorOpen = ref(false), editingId = ref(''), form = ref(emptyForm()), formError = ref(''), savedForm = ref('')
const sourceTemplates = ref([]), templateId = ref(''), templatesLoading = ref(false), templatesError = ref(false)
const testingSource = ref(''), sourceChecks = ref({})
const route = useRoute()
const hostSource = ref(null), hostExternalId = ref(''), hostReport = ref(null), selectedHosts = ref([])
const hostChecking = ref(false), hostApplying = ref(false), hostError = ref(''), hostApplied = ref(false)
const validHostItem = computed(() => /^[A-Za-z0-9_-]{1,128}$/.exec(hostExternalId.value)?.[0] === hostExternalId.value)
const hostReturnTarget = computed(() => ({ path: '/online', query: { source: hostSource.value?.id, item: hostExternalId.value } }))
const networkSource = ref(null), networkChoices = ref({}), savedNetwork = ref(''), networkError = ref('')
const emptyProxyForm = () => ({ protocol: 'http', host: '', port: '7890', username: '', password: '' })
const proxyForm = ref(emptyProxyForm())
let disposed = false, generation = 0, networkGeneration = 0, sourceTestGeneration = 0, hostGeneration = 0
let hostController = null, lastHostLink = ''
const controllers = new Set()
const selectedTemplate = computed(() => sourceTemplates.value.find(item => item.id === templateId.value))
const templateHomepage = computed(() => {
  try { const url = new URL(selectedTemplate.value?.homepage); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '' }
  catch { return '' }
})
const proxyProfiles = computed(() => profiles.value.filter(item => item.mode === 'proxy'))
const baseChoice = computed(() => resolveSourceNetworkChoice(networkChoices.value, 'default', applicationDefault.value?.rule).choice)
const selectedProxy = computed(() => profiles.value.find(item => 'profile:' + item.id === baseChoice.value))
const proxyChoice = computed(() => selectedProxy.value?.mode === 'proxy' ? selectedProxy.value.id : 'new')
const networkMode = computed(() => baseChoice.value === 'new-proxy' || selectedProxy.value?.mode === 'proxy' ? 'proxy'
  : baseChoice.value === 'direct' || selectedProxy.value?.mode === 'direct' ? 'direct' : 'unavailable')
const separateRules = computed(() => PURPOSES.filter(item => item.key !== 'default' && networkChoices.value[item.key] !== 'inherit').length)
const defaultSummary = computed(() => {
  const rule = applicationDefault.value?.rule
  if (!rule) return '暂不可读取'
  const profile = profiles.value.find(value => value.id === rule.profile_id)
  return profile ? `${profile.name} · ${rule.mode === 'proxy' ? '代理' : '直连'}` : rule.mode === 'direct' ? '直连' : '代理档案'
})
function networkSnapshot() { return JSON.stringify({ choices: networkChoices.value,
  proxy: networkChoices.value.default === 'new-proxy' ? proxyForm.value : null }) }
const dirty = computed(() => editorOpen.value && savedForm.value !== JSON.stringify(form.value)
  || !!networkSource.value && savedNetwork.value !== networkSnapshot())
useSettingsDraft({ section: 'sec-online', label: '在线视频源', dirty: () => dirty.value,
  busy: () => saving.value || toggling.value || networkSaving.value || hostApplying.value,
  discard: () => { cancelEditor(); cancelNetwork(); cancelHostCheck() } })
async function request(path, options = {}) {
  const controller = new AbortController(); controllers.add(controller)
  try { return await api(path, { ...options, signal: controller.signal }) }
  finally { controllers.delete(controller) }
}
async function hostRequest(path, options = {}) {
  const controller = new AbortController()
  hostController = controller; controllers.add(controller)
  try { return await api(path, { ...options, signal: controller.signal, timeout: 35000 }) }
  finally { controllers.delete(controller); if (hostController === controller) hostController = null }
}
function invalidateHostResult() {
  hostGeneration++; hostController?.abort(); hostController = null
  hostReport.value = null; selectedHosts.value = []; hostChecking.value = false
  hostError.value = ''; hostApplied.value = false
}
function cancelHostCheck() {
  if (hostApplying.value) return
  invalidateHostResult(); hostSource.value = null; hostExternalId.value = ''
}
function openHostCheck(source, externalId) {
  if (saving.value || networkSaving.value || hostApplying.value || dirty.value) return
  cancelEditor(); cancelNetwork(); cancelHostCheck()
  hostSource.value = source; hostExternalId.value = externalId
  const turn = hostGeneration
  void nextTick(() => {
    if (!disposed && turn === hostGeneration && typeof document !== 'undefined') document.getElementById('online-host-item')?.focus()
  })
}
function hostLinkKey() {
  const target = onlineRouteTarget(route?.query)
  return route?.query?.sec === 'sec-online' && target?.externalId ? target.sourceId + ':' + target.externalId : ''
}
function syncHostLink() {
  const key = hostLinkKey()
  if (!key) { lastHostLink = ''; cancelHostCheck(); return }
  if (key === lastHostLink || loading.value || dirty.value || saving.value || networkSaving.value || hostApplying.value) return
  const source = sources.value.find(item => item.id === route.query.source)
  if (!source) return
  lastHostLink = key; openHostCheck(source, route.query.item)
}
function hostFailure(error, applying = false) {
  if (error?.status === 409 || error?.code === 'online_source_changed') return '来源配置已变化，请重新检查后再添加。'
  if (error?.code === 'online_allowed_host_limit') return '允许的主机数量已达上限，请保留必要主机后重试。'
  if (error?.code === 'online_host_check_unsupported') return '此来源暂不支持媒体主机检查，请编辑来源的允许列表。'
  if ([401, 403].includes(error?.status)) return '请重新验证管理权限后再检查。'
  return applying ? '未能确认主机是否已保存，请重新检查后核对。' : '媒体主机检查未完成，请核对来源连接后重试。'
}
function validHostReport(value, source, externalId) {
  const hosts = values => Array.isArray(values) && values.length <= 128 && values.every(host =>
    typeof host === 'string' && host.length > 0 && host.length <= 253 && !/[\s/\\@?#%<>]/.test(host))
  return value?.source_id === source.id && value.external_id === externalId
    && /^[0-9a-f]{64}$/.test(value.configuration_fingerprint) && hosts(value.missing_hosts)
    && hosts(value.allowed_hosts) && Number.isInteger(value.rejected_count) && value.rejected_count >= 0
}
async function checkHosts() {
  if (!hostSource.value || !validHostItem.value || hostChecking.value || hostApplying.value
    || testingSource.value || !settings.value.enabled || !hostSource.value.enabled) return
  invalidateHostResult()
  const source = hostSource.value, externalId = hostExternalId.value, turn = hostGeneration
  const sourceSnapshot = JSON.stringify(source)
  hostChecking.value = true
  try {
    const report = await hostRequest('/api/online/sources/' + source.id + '/host-check', {
      method: 'POST', body: JSON.stringify({ external_id: externalId }),
    })
    if (disposed || turn !== hostGeneration || hostSource.value?.id !== source.id
      || sourceSnapshot !== JSON.stringify(sources.value.find(item => item.id === source.id))) return
    if (!validHostReport(report, source, externalId)) throw new Error('invalid host report')
    hostReport.value = report; selectedHosts.value = [...new Set(report.missing_hosts)]
  } catch (error) { if (!disposed && turn === hostGeneration) hostError.value = hostFailure(error) }
  finally { if (!disposed && turn === hostGeneration) hostChecking.value = false }
}
async function allowCheckedHosts() {
  if (!hostSource.value || !hostReport.value || !selectedHosts.value.length || hostChecking.value || hostApplying.value) return
  const sourceId = hostSource.value.id, turn = hostGeneration, report = hostReport.value
  const hosts = selectedHosts.value.filter(host => report.missing_hosts.includes(host))
  if (!hosts.length) return
  hostApplying.value = true; hostError.value = ''
  try {
    const source = await hostRequest('/api/online/sources/' + sourceId + '/allow-hosts', {
      method: 'POST', body: JSON.stringify({ hosts, expected_fingerprint: report.configuration_fingerprint }),
    })
    if (disposed || turn !== hostGeneration || hostSource.value?.id !== sourceId) return
    sources.value = sources.value.map(item => item.id === source.id ? source : item)
    hostSource.value = source; hostReport.value = null; selectedHosts.value = []; hostApplied.value = true
    delete sourceChecks.value[sourceId]
  } catch (error) {
    if (!disposed && turn === hostGeneration) {
      hostError.value = hostFailure(error, true)
      hostReport.value = null; selectedHosts.value = []
    }
  } finally { if (!disposed && turn === hostGeneration) hostApplying.value = false }
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
async function loadTemplates() {
  if (templatesLoading.value) return
  templatesLoading.value = true; templatesError.value = false
  try {
    const response = await request('/api/online/source-templates')
    if (!disposed) sourceTemplates.value = response.items || []
  } catch { if (!disposed) templatesError.value = true }
  finally { if (!disposed) templatesLoading.value = false }
}
function applyTemplate() {
  const value = selectedTemplate.value
  form.value = value ? { ...emptyForm(), name: value.name, provider: value.provider,
    api_base_url: value.api_base_url, hosts: value.allowed_hosts.join('\n'),
    search_action: value.provider_options?.search_action || 'list', detail_action: value.provider_options?.detail_action || 'detail' } : emptyForm()
}
async function testSource(source) {
  if (testingSource.value || hostChecking.value || hostApplying.value || !settings.value.enabled || !source.enabled) return
  testingSource.value = source.id
  const turn = ++sourceTestGeneration
  const fingerprint = JSON.stringify(source)
  try {
    const response = await request('/api/online/catalog/browse?' + new URLSearchParams({ source_id: source.id, page: '1' }), { timeout: 35000 })
    if (!disposed && turn === sourceTestGeneration && fingerprint === JSON.stringify(sources.value.find(item => item.id === source.id))) {
      sourceChecks.value[source.id] = response.items?.length ? '目录读取成功；可前往在线页选择影片试播。' : '目录读取成功，目前没有返回内容。'
    }
  } catch (error) {
    if (!disposed && turn === sourceTestGeneration && fingerprint === JSON.stringify(sources.value.find(item => item.id === source.id))) sourceChecks.value[source.id] = '目录测试未通过：' + onlineCatalogError(error)
  } finally { if (!disposed) testingSource.value = '' }
}
async function toggleEnabled() {
  if (hostApplying.value) return
  invalidateHostResult()
  sourceTestGeneration++
  toggling.value = true; feedback('')
  try {
    const result = await request('/api/online/settings', { method: 'PATCH', body: JSON.stringify({ enabled: !settings.value.enabled }) })
    if (disposed) return
    settings.value = result; sourceChecks.value = {}; feedback(result.enabled ? '已启用在线点播。' : '已关闭在线点播，活动在线会话已撤销。')
  } catch { if (!disposed) feedback('保存失败，请重新加载核对在线点播状态。', true) }
  finally { if (!disposed) toggling.value = false }
}
function editSource(source = null) {
  if (saving.value || networkSaving.value || hostApplying.value) return
  cancelHostCheck()
  networkGeneration++; cancelNetwork()
  editingId.value = source?.id || ''; formError.value = ''
  templateId.value = ''
  form.value = source ? { name: source.name, provider: source.provider, api_base_url: source.api_base_url,
    hosts: source.allowed_hosts.join('\n'), enabled: source.enabled, priority: source.priority,
    search_action: source.provider_options?.search_action || 'list', detail_action: source.provider_options?.detail_action || 'detail' } : emptyForm()
  savedForm.value = JSON.stringify(form.value); editorOpen.value = true
  if (!source && !sourceTemplates.value.length) loadTemplates()
}
function providerChanged() {
  if (form.value.provider === 'open_catalog') {
    form.value.api_base_url = OPEN_CATALOG_BASE
    form.value.hosts = 'www.bigbuckbunny.org\nvideo.blender.org'
    if (!form.value.name.trim()) form.value.name = '开放样例目录'
  } else { form.value.api_base_url = ''; form.value.hosts = '' }
}
function cancelEditor() { editorOpen.value = false; editingId.value = ''; templateId.value = ''; formError.value = ''; form.value = emptyForm(); savedForm.value = '' }
async function saveSource() {
  if (saving.value || hostApplying.value) return
  invalidateHostResult()
  sourceTestGeneration++
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
    delete sourceChecks.value[source.id]
    cancelEditor(); feedback('来源已保存；尚未验证上游连通性。')
  } catch { if (!disposed) formError.value = '保存失败，请检查名称、接口、允许主机和字段格式；若请求超时，请刷新后核对是否已经保存。' }
  finally { if (!disposed) saving.value = false }
}
async function editNetwork(source) {
  if (saving.value || networkSaving.value || hostApplying.value) return
  cancelHostCheck()
  cancelEditor(); const turn = ++networkGeneration
  networkSource.value = null; networkError.value = ''
  try {
    const response = await request('/api/online/sources/' + source.id + '/network')
    if (disposed || turn !== networkGeneration) return
    networkChoices.value = Object.fromEntries(PURPOSES.map(purpose => [purpose.key, networkRuleChoice(response.policy[purpose.key])]))
    proxyForm.value = emptyProxyForm()
    savedNetwork.value = networkSnapshot(); networkSource.value = source
  } catch { if (!disposed && turn === networkGeneration) networkError.value = '此来源的网络规则暂不可读取，请重试。' }
}
function cancelNetwork() { networkGeneration++; networkSource.value = null; networkChoices.value = {}; savedNetwork.value = ''; proxyForm.value = emptyProxyForm() }
function changeNetworkMode(mode) {
  if (mode === 'direct') { networkChoices.value.default = 'direct'; proxyForm.value = emptyProxyForm() }
  else if (mode === 'proxy') networkChoices.value.default = proxyProfiles.value[0] ? 'profile:' + proxyProfiles.value[0].id : 'new-proxy'
}
function selectProxy(id) {
  networkChoices.value.default = id === 'new' ? 'new-proxy' : 'profile:' + id
  proxyForm.value = emptyProxyForm()
}
function inheritAllPurposes() { for (const purpose of PURPOSES) if (purpose.key !== 'default') networkChoices.value[purpose.key] = 'inherit' }
function purposeSummary(purpose) {
  const resolved = resolveSourceNetworkChoice(networkChoices.value, purpose, applicationDefault.value?.rule)
  const inherited = resolved.from === purpose ? '单独设置' : resolved.from === 'application' ? '沿用服务默认' : '沿用来源默认'
  return networkChoiceSummary(resolved.choice, profiles.value) + '（' + inherited + '）'
}
async function saveNetwork() {
  if (networkSaving.value || !networkSource.value || hostApplying.value) return
  invalidateHostResult()
  sourceTestGeneration++
  networkSaving.value = true; networkError.value = ''
  let createdProfile = false
  try {
    if (networkChoices.value.default === 'new-proxy') {
      const profile = await request('/api/online/network/profiles', { method: 'POST', body: JSON.stringify(proxyProfilePayload(proxyForm.value, networkSource.value.name)) })
      if (disposed) return
      profiles.value = [...profiles.value.filter(item => item.id !== profile.id), profile]
      // Retain the created profile if policy saving fails, so retry never creates
      // another profile or keeps write-only credentials in the form.
      networkChoices.value.default = 'profile:' + profile.id
      proxyForm.value = emptyProxyForm()
      createdProfile = true
    }
    const rules = Object.fromEntries(PURPOSES.map(purpose => [purpose.key, choiceNetworkRule(networkChoices.value[purpose.key], profiles.value)]))
    await request('/api/online/sources/' + networkSource.value.id + '/network', { method: 'PATCH', body: JSON.stringify(rules) })
    if (disposed) return
    delete sourceChecks.value[networkSource.value.id]
    cancelNetwork(); feedback('网络规则已保存；后续请求按各用途的新规则建立连接。')
  } catch (error) {
    if (!disposed) networkError.value = createdProfile ? '代理配置已保存，但来源网络规则未保存。请重试“保存网络规则”。'
      : error.message?.startsWith('请填写代理') || error.message?.startsWith('代理服务器') || error.message === '代理认证信息格式无效。'
        ? error.message : '网络规则保存失败，请检查代理地址、端口及档案是否有效后重试。'
  }
  finally { if (!disposed) networkSaving.value = false }
}
watch(hostExternalId, invalidateHostResult, { flush: 'sync' })
watch(() => [route?.query?.sec, route?.query?.source, route?.query?.item, loading.value, sources.value, dirty.value, saving.value, networkSaving.value, hostApplying.value], syncHostLink, { immediate: true, flush: 'post' })
onMounted(load)
onUnmounted(() => { disposed = true; generation++; networkGeneration++; hostGeneration++; controllers.forEach(controller => controller.abort()); controllers.clear() })
</script>
