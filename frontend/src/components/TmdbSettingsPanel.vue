<template>
  <section class="card-block tmdb-settings">
    <div class="tmdb-heading"><div><h3>TMDB</h3><p class="hint">介绍、海报与演职员资料。</p></div><span class="credential-state">{{ s?.tmdb_configured ? '凭据已配置' : '未配置凭据' }}</span></div>
    <p v-if="!s" role="status">正在加载配置…</p>
    <fieldset :disabled="busy || !s">
      <div class="tmdb-credentials">
        <JzField id="tmdb-read-token" label="读取令牌（Read Token）" :hint="s?.tmdb_configured ? '留空保留现有凭据 · ' + sourceText(s.tmdb_read_token_masked ? s.tmdb_read_token_source : s.tmdb_api_key_source) + ' ' + (s.tmdb_read_token_masked || '') : '没有令牌也可先用本地资料和备用来源入库。'" v-slot="field">
          <input :id="field.id" :aria-describedby="field.describedby" v-model="form.readToken" name="tmdb-read-token" type="password" autocomplete="off" :spellcheck="false" placeholder="粘贴 TMDB API Read Access Token" />
        </JzField>
        <a class="token-help" href="https://developer.themoviedb.org/docs/authentication-application" target="_blank" rel="noopener noreferrer">如何获取读取令牌 <AppIcon name="external-link" :size="14" /></a>
      </div>
      <details class="tmdb-options">
        <summary><span>API Key、代理与其他配置</span><small>可选</small></summary>
        <div class="tmdb-option-fields">
          <JzField id="tmdb-api-key" label="API Key" :hint="(s?.tmdb_api_key_masked || '未设置 API Key') + ' · Read Token 优先'" v-slot="field">
            <input :id="field.id" :aria-describedby="field.describedby" v-model="form.apiKey" name="tmdb-api-key" type="password" autocomplete="off" :spellcheck="false" placeholder="没有 Read Token 时使用；留空保留" />
          </JzField>
          <JzField id="tmdb-language" label="资料语言"><input id="tmdb-language" v-model="form.language" name="tmdb-language" autocomplete="off" placeholder="例如 zh-CN" /></JzField>
          <JzField id="tmdb-proxy" label="服务器网络代理"><input id="tmdb-proxy" v-model="form.proxy" name="tmdb-proxy" type="url" autocomplete="off" :spellcheck="false" placeholder="http://服务器:端口" /></JzField>
          <JzField id="tmdb-image-base" label="图片服务地址"><input id="tmdb-image-base" v-model="form.imageBase" name="tmdb-image-base" type="url" autocomplete="off" :spellcheck="false" placeholder="https://image.tmdb.org" /></JzField>
        </div>
        <p class="hint">保存后优先使用这里的配置。清空代理或图片地址时，恢复服务器配置或默认值。</p>
        <dl><dt>代理来源</dt><dd>{{ sourceText(s?.tmdb_proxy_source) }}</dd><dt>语言来源</dt><dd>{{ sourceText(s?.tmdb_language_source) }}</dd><dt>图片地址来源</dt><dd>{{ sourceText(s?.tmdb_image_base_source) }}</dd></dl>
        <div class="bar"><JzButton icon="undo" @click="clear">{{ armClear ? '确认恢复服务器配置' : '恢复服务器配置' }}</JzButton><JzButton v-if="armClear" @click="armClear = false">取消恢复</JzButton></div>
        <p v-if="armClear" class="hint">将移除在此保存的五项 TMDB 配置，改用服务器环境配置或默认值。</p>
      </details>
      <div class="bar tmdb-actions">
        <JzButton variant="primary" :loading="pending === 'test'" :disabled="busy" @click="save(true)">保存并测试 TMDB 连接</JzButton>
        <JzButton :loading="pending === 'save'" :disabled="busy" @click="save(false)">保存配置</JzButton>
      </div>
    </fieldset>
    <div v-if="busy || message" class="tmdb-result" :class="resultKind" role="status"><strong>{{ busy ? '正在处理…' : resultKind === 'error' ? '连接或保存未完成' : resultKind === 'success' ? '连接验证通过' : '配置已更新' }}</strong><span v-if="!busy">{{ message }}</span></div>
  </section>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

import { computed, reactive, ref, watch } from 'vue'
import { useSettingsDraft } from '../settingsDrafts.js'
import { api } from '../api.js'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
const props = defineProps({ settings: { type: Object, default: null } })
const emit = defineEmits(['saved', 'validated', 'busy'])
const s = ref(null)
const form = reactive({ readToken: '', apiKey: '', proxy: '', language: '', imageBase: '' })
const busy = ref(false)
const pending = ref('')
const message = ref('')
const resultKind = ref('saved')
const armClear = ref(false)
let generation = 0
const sourceText = source => ({ db: '设置页', env: '服务器环境配置', default: '系统默认', unset: '未设置' }[source] || '')
const baseline = ref('')
const dirty = computed(() => !!s.value && JSON.stringify(form) !== baseline.value)
function sync(value, preserveDraft = false) {
  const previous = baseline.value ? JSON.parse(baseline.value) : {}
  const next = { readToken: '', apiKey: '', proxy: value?.tmdb_proxy || '',
    language: value?.tmdb_language || '', imageBase: value?.tmdb_image_base || '' }
  for (const key of Object.keys(next)) {
    if (!preserveDraft || form[key] === previous[key]) form[key] = next[key]
  }
  s.value = value
  baseline.value = JSON.stringify(next)
}
function discard() { sync(s.value); armClear.value = false; message.value = '' }
useSettingsDraft({ section: 'sec-tmdb', label: 'TMDB 连接配置', dirty: () => dirty.value,
  busy: () => busy.value, discard })
watch(() => props.settings, value => sync(value, dirty.value), { immediate: true })
watch(form, () => { generation++; message.value = ''; emit('validated', false) }, { flush: 'sync' })
watch(busy, value => emit('busy', value), { flush: 'sync' })
function payload() {
  const out = {}
  if (form.readToken.trim()) out.tmdb_read_token = form.readToken.trim()
  if (form.apiKey.trim()) out.tmdb_api_key = form.apiKey.trim()
  for (const [key, field] of [['tmdb_proxy', 'proxy'], ['tmdb_language', 'language'], ['tmdb_image_base', 'imageBase']]) {
    if (form[field].trim() !== (s.value?.[key] || '')) out[key] = form[field].trim()
  }
  return out
}
async function save(test) {
  if (busy.value || !s.value) return
  busy.value = true
  pending.value = test ? 'test' : 'save'
  emit('validated', false)
  try {
    const body = payload()
    if (Object.keys(body).length) {
      const value = await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) })
      form.readToken = ''; form.apiKey = ''
      sync(value); emit('saved', value)
    }
    // Whitespace-only edits also return to the saved values.
    sync(s.value)
    message.value = '配置已保存'
    resultKind.value = 'saved'
    if (test) {
      const gen = generation
      const result = await api('/api/tmdb/check', { method: 'POST', timeout: 15000 })
      if (gen !== generation) return
      resultKind.value = result.ok ? 'success' : 'error'
      message.value = result.message + ' · ' + result.elapsed_ms + ' ms'
      emit('validated', result.ok)
    }
  } catch (e) { resultKind.value = 'error'; message.value = '操作失败：' + e.message }
  finally { busy.value = false; pending.value = '' }
}
async function clear() {
  if (busy.value || !s.value) return
  if (!armClear.value) { armClear.value = true; return }
  armClear.value = false; busy.value = true; emit('validated', false)
  try {
    const value = await api('/api/settings', { method: 'PUT', body: JSON.stringify({
      tmdb_read_token: '', tmdb_api_key: '', tmdb_proxy: '', tmdb_language: '', tmdb_image_base: '',
    }) })
    form.readToken = ''; form.apiKey = ''
    sync(value); emit('saved', value)
    resultKind.value = 'saved'
    message.value = '已恢复服务器配置或默认值'
  } catch (e) { resultKind.value = 'error'; message.value = '恢复失败：' + e.message }
  finally { busy.value = false }
}
</script>
<style scoped>
.tmdb-settings { --jz-control-current: var(--jz-control-height); line-height: 1.7; }
.tmdb-heading { display: flex; justify-content: space-between; align-items: start; gap: 16px; margin-bottom: 20px; }
.tmdb-heading h3 { margin: 0 0 4px; }
.tmdb-heading p { margin: 0; }
.credential-state { color: var(--jz-text-dim); font-size: var(--jz-font-s); padding: 4px 8px; background: var(--jz-surface-2); border-radius: var(--jz-radius-s); white-space: nowrap; }
fieldset { max-width: var(--jz-form-width); border: 0; padding: 0; margin: 0; min-width: 0; }
input { width: 100%; box-sizing: border-box; }
.token-help { display: inline-block; font-size: var(--jz-font-s); margin-top: 8px; }
.tmdb-options { border-top: 1px solid var(--jz-border); border-bottom: 1px solid var(--jz-border); margin-top: 24px; padding: 12px 0; }
summary { cursor: pointer; font-size: var(--jz-font-m); }
summary small { color: var(--jz-text-dim); margin-left: 12px; font-size: var(--jz-font-s); }
.tmdb-option-fields { display: grid; gap: 20px; margin: 20px 0; }
.hint { color: var(--jz-text-dim); font-size: var(--jz-font-m); }
dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
dd { margin: 0; }
.bar { display: flex; flex-wrap: wrap; gap: 10px; margin: 16px 0 0; }
a { color: var(--jz-link); }
.tmdb-result { display: flex; flex-direction: column; gap: 4px; max-width: var(--jz-form-width); box-sizing: border-box; padding: 14px 16px; margin-top: 20px; border-left: 3px solid var(--jz-info-border); background: var(--jz-info-soft); font-size: var(--jz-font-m); overflow-wrap: anywhere; }
.tmdb-result span { color: var(--jz-text-dim); }
.tmdb-result.success { border-color: var(--jz-success-border); background: var(--jz-success-soft); }
.tmdb-result.error { border-color: var(--jz-danger-border); background: var(--jz-danger-soft); }
@media (max-width: 700px) {
  .tmdb-settings { --jz-control-current: var(--jz-touch-target); }
  .tmdb-heading { margin-bottom: 16px; }
  .tmdb-heading .hint { max-width: 24ch; }
  input, button { min-height: var(--jz-touch-target); }
  .tmdb-options { margin-top: 20px; padding: 0; }
  summary { min-height: 44px; box-sizing: border-box; padding: 10px 0; }
  .bar { gap: 8px; margin-top: 16px; padding: 0; }
}
@media (pointer: coarse) { .tmdb-settings { --jz-control-current: var(--jz-touch-target); } }
</style>
