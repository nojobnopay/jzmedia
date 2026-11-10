<template>
  <section class="card-block ai-settings">
    <p v-if="!settings && busy" role="status">正在加载智能辅助配置…</p>
    <fieldset :disabled="busy || !settings">
      <div class="ai-enable"><div><h3>搜索与匹配建议</h3><p class="hint">理解中文搜索，为难匹配的片名提供候选建议。</p></div><label class="check"><input v-model="form.enabled" type="checkbox" />启用智能辅助</label></div>
      <p class="ai-privacy">仅在你使用智能功能时调用；发送搜索、片名等文本，不上传视频。</p>
      <div v-if="!form.enabled" class="ai-disabled"><span>当前关闭。可以先配置并测试服务，保存后再启用。</span><JzButton type="button" :aria-expanded="configure" aria-controls="ai-service-fields" @click="configure = !configure">{{ configure ? '收起服务配置' : '配置与测试服务' }}</JzButton></div>
      <div v-if="form.enabled || configure" id="ai-service-fields">
        <div class="ai-fields">
          <JzField id="ai-provider" label="服务商"><select id="ai-provider" v-model="form.provider" @change="selectProvider"><option value="deepseek">DeepSeek</option><option value="opencode_go">OpenCode Go</option><option value="compatible">自定义兼容接口</option></select></JzField>
          <JzField id="ai-model" label="模型名称" :error="fieldErrors.model" v-slot="field"><input :id="field.id" v-model="form.model" name="ai-model" autocomplete="off" :aria-describedby="field.describedby" :aria-invalid="field.invalid || undefined" :placeholder="preset.model || '模型 ID'" /></JzField>
          <JzField class="wide" id="ai-base-url" label="API 基础地址" :error="fieldErrors.base_url" v-slot="field"><input :id="field.id" v-model="form.base_url" name="ai-base-url" type="url" autocomplete="off" :spellcheck="false" :aria-describedby="field.describedby" :aria-invalid="field.invalid || undefined" :placeholder="preset.base_url || 'http://localhost:11434/v1'" /></JzField>
          <JzField class="wide" id="ai-api-key" label="API Key"><input id="ai-api-key" v-model="apiKey" name="ai-api-key" type="password" autocomplete="new-password" placeholder="留空保留已有密钥；本地服务可不填" aria-describedby="ai-key-hint" /></JzField>
        </div>
        <p id="ai-key-hint" class="hint">{{ settings?.api_key_set ? '密钥已配置 · ' + sourceText(settings.api_key_source) : '未配置密钥' }} {{ settings?.api_key_masked }}</p>
        <p class="hint">切换服务商后请填写对应密钥；留空沿用当前密钥。</p>
        <div v-if="form.provider === 'opencode_go'" class="provider-note"><p>OpenCode Go 官方面向编码代理；jzmedia 的影视用途尚未验证是否可用。<a href="https://opencode.ai/docs/go/#where-can-i-use-it" target="_blank" rel="noopener noreferrer">查看官方使用范围 <AppIcon name="external-link" :size="14" /></a></p><p>模型使用 Chat Completions 对应 ID，例如 glm-5.3-flash，不加 opencode-go/ 前缀。</p></div>
        <details ref="advanced" class="ai-advanced"><summary>调用限额、超时与密钥管理</summary>
          <div class="ai-fields">
            <JzField id="ai-timeout" label="单次超时（秒）" :error="fieldErrors.timeout_seconds" v-slot="field"><input :id="field.id" v-model.number="form.timeout_seconds" name="ai-timeout" type="number" inputmode="numeric" min="2" max="60" step="1" :aria-describedby="field.describedby" :aria-invalid="field.invalid || undefined" /></JzField>
            <JzField id="ai-daily-limit" label="每日调用上限" :error="fieldErrors.daily_limit" v-slot="field"><input :id="field.id" v-model.number="form.daily_limit" name="ai-daily-limit" type="number" inputmode="numeric" min="1" max="10000" step="1" :aria-describedby="field.describedby" :aria-invalid="field.invalid || undefined" /></JzField>
          </div>
          <p class="hint">这里只保存一套配置。API Key 仅由服务器使用；本地兼容服务可在本机推理。自定义服务需要提供兼容 Chat Completions 的地址与模型。</p>
          <template v-if="settings?.api_key_source === 'db'"><div class="bar"><JzButton @click="clearKey" type="button" icon="delete">{{ armClear ? '确认移除已保存密钥' : '移除已保存密钥' }}</JzButton><JzButton v-if="armClear" @click="armClear = false" type="button">取消</JzButton></div><p v-if="armClear" class="hint">将移除此处保存的密钥；服务器环境中已有密钥时会恢复使用它。</p></template>
        </details>
      </div>
      <p v-if="attempted && Object.keys(validationErrors).length" class="validation-summary" role="alert">请修正标出的配置后保存。</p>
      <SettingsFormActions v-if="form.enabled || configure || dirty" class="ai-actions" :dirty="dirty" :busy="busy" :status="error ? '操作未完成，请查看错误提示' : message ? '设置操作已完成' : ''" label="智能辅助配置操作">
        <JzButton variant="primary" :disabled="busy || !settings" :loading="busy" @click="save">保存智能辅助配置</JzButton>
        <JzButton v-if="dirty" :disabled="busy" @click="discard">放弃修改</JzButton>
        <JzButton v-if="form.enabled || configure" :disabled="busy || dirty" @click="check">测试已保存连接</JzButton>
      </SettingsFormActions>
    </fieldset>
    <div v-if="busy || error || message" class="ai-result" :class="{ error: !!error }" role="status">{{ busy ? '正在处理…' : error || message }}</div>
    <div v-if="settings?.usage" class="ai-usage"><span>今日调用 <b>{{ settings.usage.requests }}</b> / {{ settings.daily_limit }} 次</span><details><summary>查看用量说明</summary><p class="hint">输入 {{ settings.usage.input_tokens }}、输出 {{ settings.usage.output_tokens }} tokens。按 UTC 日重置；失败和连接测试计次，缓存命中不计。费用以服务商账单为准。</p></details></div>
    <JzButton v-if="!settings && !busy" @click="load" type="button" icon="refresh">重新加载配置</JzButton>
  </section>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useSettingsDraft } from '../settingsDrafts.js'
import { useAiRequest } from '../useAiRequest.js'
import { aiProviderPreset } from '../aiProviders.js'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import SettingsFormActions from './SettingsFormActions.vue'
const fields = ['enabled', 'provider', 'base_url', 'model', 'timeout_seconds', 'daily_limit']
const settings = ref(null)
const form = reactive({ enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', timeout_seconds: 12, daily_limit: 100 })
const apiKey = ref('')
const message = ref('')
const armClear = ref(false)
const configure = ref(false)
const advanced = ref(null), attempted = ref(false)
const { busy, error, run } = useAiRequest()
const preset = computed(() => aiProviderPreset(form.provider))
const dirty = computed(() => !!settings.value && (!!apiKey.value || fields.some(key => form[key] !== settings.value[key])))
const validationErrors = computed(() => {
  const errors = {}
  if (!form.model.trim()) errors.model = '请填写模型名称。'
  try { const url = new URL(form.base_url.trim()); if (!['http:', 'https:'].includes(url.protocol)) throw new Error('protocol') }
  catch { errors.base_url = '请填写完整的 HTTP 或 HTTPS 服务地址。' }
  if (!Number.isInteger(form.timeout_seconds) || form.timeout_seconds < 2 || form.timeout_seconds > 60) errors.timeout_seconds = '超时应为 2–60 秒的整数。'
  if (!Number.isInteger(form.daily_limit) || form.daily_limit < 1 || form.daily_limit > 10000) errors.daily_limit = '每日调用上限应为 1–10000 的整数。'
  return errors
})
const fieldErrors = computed(() => attempted.value ? validationErrors.value : {})
const sourceText = source => ({ db: '设置页', env: '服务器环境配置' }[source] || '')
function selectProvider() {
  Object.assign(form, aiProviderPreset(form.provider))
  message.value = ''
  armClear.value = false
  attempted.value = false
}
function sync(value, preserveDraft = false) {
  for (const key of fields) {
    if (!preserveDraft || form[key] === settings.value?.[key]) form[key] = value[key]
  }
  settings.value = value
  if (!preserveDraft) { apiKey.value = ''; attempted.value = false }
  armClear.value = false
}
function discard() { if (busy.value || !settings.value) return; sync(settings.value); message.value = ''; error.value = '' }
useSettingsDraft({ section: 'sec-ai', label: '智能辅助配置', dirty: () => dirty.value,
  busy: () => !!settings.value && busy.value, discard })
async function load() {
  const value = await run('/api/ai/settings', undefined, 'GET')
  if (value) sync(value)
}
async function save() {
  if (busy.value || !settings.value) return
  attempted.value = true
  const invalid = Object.keys(validationErrors.value)
  if (invalid.length) {
    configure.value = true
    await nextTick()
    if ((validationErrors.value.timeout_seconds || validationErrors.value.daily_limit) && advanced.value) advanced.value.open = true
    const ids = { model: 'ai-model', base_url: 'ai-base-url', timeout_seconds: 'ai-timeout', daily_limit: 'ai-daily-limit' }
    if (typeof document !== 'undefined') {
      const field = document.getElementById(ids[invalid[0]])
      field?.focus({ preventScroll: true })
      field?.scrollIntoView({ block: 'center', behavior: 'auto' })
    }
    return
  }
  message.value = ''
  const body = { ...form, base_url: form.base_url.trim(), model: form.model.trim() }
  if (apiKey.value.trim()) body.api_key = apiKey.value.trim()
  const value = await run('/api/ai/settings', body, 'PATCH')
  if (value) { sync(value); message.value = value.enabled ? '配置已保存，智能辅助已开启' : '配置已保存，智能辅助已关闭' }
}
async function check() {
  if (dirty.value) return
  message.value = ''
  const value = await run('/api/ai/check')
  if (value) { message.value = value.message; if (value.usage) settings.value.usage = value.usage }
  else {
    // A failed connection test also consumes a request; refresh its counter without hiding the error.
    const failure = error.value
    const state = await run('/api/ai/settings', undefined, 'GET')
    if (state) sync(state, true)
    error.value = failure
  }
}
async function clearKey() {
  if (!armClear.value) { armClear.value = true; return }
  message.value = ''
  const value = await run('/api/ai/settings', { clear_api_key: true }, 'PATCH')
  if (value) { sync(value, true); message.value = value.api_key_source === 'env' ? '已移除设置页密钥，现使用服务器环境密钥' : '已移除设置页密钥' }
}
onMounted(load)
</script>
<style scoped>
.ai-settings { line-height: 1.7; }
fieldset { max-width: var(--jz-form-width); min-width: 0; border: 0; padding: 0; margin: 0; }
.ai-enable { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.ai-enable h3 { margin-bottom: 6px; }
.ai-enable .hint { margin: 0; }
.ai-privacy { margin: 12px 0 16px; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.ai-disabled { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 0; border-top: 1px solid var(--jz-border); font-size: var(--jz-font-m); color: var(--jz-text-dim); }
.ai-disabled button { flex-shrink: 0; }
.ai-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin: 16px 0 12px; }
.ai-fields :deep(.jz-field) { margin-block: 0; }
.validation-summary { color: var(--jz-danger); font-size: var(--jz-font-m); }
label { display: flex; flex-direction: column; gap: 8px; font-size: var(--jz-font-m); }
input, select { width: 100%; min-width: 0; box-sizing: border-box; }
.wide { grid-column: 1 / -1; }
.check { flex-direction: row; align-items: center; flex-shrink: 0; min-height: 44px; }
.check input { width: 16px; height: 16px; }
.field-hint, .hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.provider-note { padding: 12px 16px; background: var(--jz-warn-soft); border-left: 3px solid var(--jz-warn-border); font-size: var(--jz-font-s); }
.provider-note p { margin: 0; }
.provider-note p + p { margin-top: 6px; }
a { color: var(--jz-link); }
.bar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 16px 0 0; }
.bar .hint { margin: 0; }
.ai-advanced { margin-top: 20px; padding: 12px 0; border-top: 1px solid var(--jz-border); border-bottom: 1px solid var(--jz-border); }
summary { cursor: pointer; font-size: var(--jz-font-m); }
.ai-result { margin-top: 20px; padding: 12px 16px; max-width: var(--jz-form-width); box-sizing: border-box; background: var(--jz-info-soft); border-left: 3px solid var(--jz-info-border); font-size: var(--jz-font-m); overflow-wrap: anywhere; }
.ai-result.error { border-color: var(--jz-danger-border); background: var(--jz-danger-soft); }
.ai-usage { max-width: var(--jz-form-width); margin-top: 28px; padding-top: 16px; border-top: 1px solid var(--jz-border); font-size: var(--jz-font-m); color: var(--jz-text-dim); }
.ai-usage b { font-variant-numeric: tabular-nums; color: var(--jz-text); }
.ai-usage details { margin-top: 8px; }
.ai-usage summary { font-size: var(--jz-font-s); }
@media (max-width: 700px) { .ai-fields { grid-template-columns: minmax(0, 1fr); } .ai-enable, .ai-disabled { align-items: start; flex-direction: column; gap: 12px; } .ai-privacy { margin-bottom: 16px; } .ai-advanced { padding: 0; } summary { min-height: 44px; box-sizing: border-box; padding: 10px 0; } }
</style>
