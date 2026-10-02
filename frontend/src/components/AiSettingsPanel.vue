<template>
  <section class="card-block ai-settings">
    <h3>智能辅助（可选）</h3>
    <p>帮助理解中文搜索、分析难匹配的片名并解释候选。默认关闭，仅手动点击智能搜索、AI 匹配建议或测试连接时调用。</p>
    <p class="hint">云端服务会收到搜索语句，或片名、文件名与候选资料等必要文本，不上传视频。已保存的 API Key 仅在服务器使用；使用本地兼容服务可在本机推理。</p>
    <p v-if="!settings && busy" role="status">正在加载智能辅助配置…</p>
    <fieldset :disabled="busy || !settings">
      <label class="check"><input v-model="form.enabled" type="checkbox" />启用智能辅助</label>
      <div class="ai-fields">
        <label>服务商<select v-model="form.provider" @change="selectProvider"><option value="deepseek">DeepSeek</option><option value="opencode_go">OpenCode Go</option><option value="compatible">自定义兼容接口</option></select></label>
        <label>模型名称<input v-model="form.model" autocomplete="off" :placeholder="preset.model || '模型 ID'" /></label>
        <label class="wide">API 基础地址<input v-model="form.base_url" type="url" autocomplete="off" :placeholder="preset.base_url || 'http://localhost:11434/v1'" /></label>
        <label class="wide">API Key<input v-model="apiKey" type="password" autocomplete="new-password" placeholder="留空保留已有密钥；本地服务可不填" /></label>
        <label>单次超时（秒）<input v-model.number="form.timeout_seconds" type="number" min="2" max="60" step="1" /></label>
        <label>每日调用上限<input v-model.number="form.daily_limit" type="number" min="1" max="10000" step="1" /></label>
      </div>
      <p class="hint">{{ settings?.api_key_set ? '密钥已配置 · ' + sourceText(settings.api_key_source) : '未配置密钥' }} {{ settings?.api_key_masked }}</p>
      <p class="hint">这里只保存一套有效配置，不按服务商分别保存密钥。切换服务商后请填写对应 API Key；留空会沿用当前密钥。</p>
      <template v-if="form.provider === 'opencode_go'">
        <p class="hint">OpenCode Go 官方面向编码代理；jzmedia 的影视用途尚未验证是否可用。<a href="https://opencode.ai/docs/go/#where-can-i-use-it" target="_blank" rel="noopener noreferrer">查看官方使用范围 ↗</a></p>
        <p class="hint">本版仅使用 Chat Completions。模型填写官方对应的 ID，例如 glm-5.3-flash，不加 opencode-go/ 前缀；可修改为其他支持该接口的模型。</p>
      </template>
      <p v-else class="hint">地址填写到 API 根目录，例如 https://api.deepseek.com 或 http://localhost:11434/v1。自定义服务需提供兼容 Chat Completions 的地址与模型。</p>
      <div class="bar"><button :disabled="!valid" @click="save">保存智能辅助配置</button><button :disabled="dirty" @click="check">测试已保存连接</button></div>
      <p v-if="dirty" class="hint">配置有修改，请先保存再测试连接。</p>
      <template v-if="settings?.api_key_source === 'db'">
        <button @click="clearKey">{{ armClear ? '确认移除已保存密钥' : '移除已保存密钥' }}</button>
        <button v-if="armClear" @click="armClear = false">取消</button>
        <p v-if="armClear" class="hint">将移除此处保存的密钥；服务器环境中已有密钥时会恢复使用它。</p>
      </template>
    </fieldset>
    <p v-if="settings?.usage" class="hint">今日 {{ settings.usage.requests }} / {{ settings.daily_limit }} 次 · 输入 {{ settings.usage.input_tokens }}、输出 {{ settings.usage.output_tokens }} tokens。按 UTC 日重置；失败和连接测试计次，缓存命中不计。费用以服务商账单为准。</p>
    <p v-if="busy || error || message" role="status">{{ busy ? '正在处理…' : error || message }}</p>
    <button v-if="!settings && !busy" @click="load">重新加载配置</button>
  </section>
</template>
<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useAiRequest } from '../useAiRequest.js'
import { aiProviderPreset } from '../aiProviders.js'
const fields = ['enabled', 'provider', 'base_url', 'model', 'timeout_seconds', 'daily_limit']
const settings = ref(null)
const form = reactive({ enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', timeout_seconds: 12, daily_limit: 100 })
const apiKey = ref('')
const message = ref('')
const armClear = ref(false)
const { busy, error, run } = useAiRequest()
const preset = computed(() => aiProviderPreset(form.provider))
const dirty = computed(() => !!apiKey.value || fields.some(key => form[key] !== settings.value?.[key]))
const valid = computed(() => form.model.trim() && /^https?:\/\//i.test(form.base_url.trim()) && Number.isInteger(form.timeout_seconds) && form.timeout_seconds >= 2 && form.timeout_seconds <= 60 && Number.isInteger(form.daily_limit) && form.daily_limit >= 1 && form.daily_limit <= 10000)
const sourceText = source => ({ db: '设置页', env: '服务器环境配置' }[source] || '')
function selectProvider() {
  Object.assign(form, aiProviderPreset(form.provider))
  message.value = ''
  armClear.value = false
}
function sync(value) {
  settings.value = value
  for (const key of fields) form[key] = value[key]
  apiKey.value = ''
  armClear.value = false
}
async function load() {
  const value = await run('/api/ai/settings', undefined, 'GET')
  if (value) sync(value)
}
async function save() {
  if (!valid.value) return
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
    if (state) settings.value = state
    error.value = failure
  }
}
async function clearKey() {
  if (!armClear.value) { armClear.value = true; return }
  message.value = ''
  const value = await run('/api/ai/settings', { clear_api_key: true }, 'PATCH')
  if (value) { sync(value); message.value = value.api_key_source === 'env' ? '已移除设置页密钥，现使用服务器环境密钥' : '已移除设置页密钥' }
}
onMounted(load)
</script>
<style scoped>
.ai-settings { line-height: 1.7; }
fieldset { min-width: 0; border: 0; padding: 0; margin: 0; }
.ai-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }
label { display: flex; flex-direction: column; gap: 6px; }
input, select { width: 100%; min-width: 0; box-sizing: border-box; }
.wide { grid-column: 1 / -1; }
.check { flex-direction: row; align-items: center; }
.check input { width: auto; }
.hint { color: var(--jz-text-dim); font-size: .875rem; }
a { color: var(--jz-link); }
.bar { display: flex; flex-wrap: wrap; gap: 10px; padding: 12px 0; }
@media (max-width: 600px) { .ai-fields { grid-template-columns: minmax(0, 1fr); } }
</style>
