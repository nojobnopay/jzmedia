<template>
  <section class="card-block tmdb-settings">
    <h3>配置 TMDB 资料来源</h3>
    <p>用于获取电影和剧集的介绍、海报与演职员。没有令牌也可先用本地资料和备用来源入库。</p>
    <p><a href="https://developer.themoviedb.org/docs/authentication-application" target="_blank" rel="noopener noreferrer">如何获取读取令牌 ↗</a>：登录 TMDB，在账户的 API 设置中复制 API Read Access Token。</p>
    <p v-if="!s" role="status">正在加载配置…</p>
    <fieldset :disabled="busy || !s">
      <label>读取令牌（Read Token）<input v-model="form.readToken" type="password" autocomplete="off" placeholder="粘贴令牌；留空保留现有配置" /></label>
      <p class="hint">{{ s?.tmdb_configured ? '已配置凭据 · ' + sourceText(s.tmdb_read_token_masked ? s.tmdb_read_token_source : s.tmdb_api_key_source) : '尚未配置凭据' }} {{ s?.tmdb_read_token_masked }}</p>
      <details>
        <summary>API Key、代理与其他配置</summary>
        <label>API Key<input v-model="form.apiKey" type="password" autocomplete="off" placeholder="没有 Read Token 时使用；留空保留" /></label>
        <p class="hint">{{ s?.tmdb_api_key_masked || '未设置 API Key' }} · Read Token 优先</p>
        <label>资料语言<input v-model="form.language" placeholder="zh-CN" /></label>
        <label>服务器网络代理<input v-model="form.proxy" autocomplete="off" placeholder="http://服务器:端口" /></label>
        <label>图片服务地址<input v-model="form.imageBase" placeholder="https://image.tmdb.org" /></label>
        <p class="hint">在此保存立即生效，优先于服务器环境配置；清空代理或图片地址会恢复服务器配置或默认值。</p>
        <dl><dt>代理来源</dt><dd>{{ sourceText(s?.tmdb_proxy_source) }}</dd><dt>语言来源</dt><dd>{{ sourceText(s?.tmdb_language_source) }}</dd><dt>图片地址来源</dt><dd>{{ sourceText(s?.tmdb_image_base_source) }}</dd></dl>
        <button @click="clear">{{ armClear ? '确认恢复服务器配置' : '恢复服务器配置' }}</button>
        <button v-if="armClear" @click="armClear = false">取消恢复</button>
        <p v-if="armClear">将移除在此保存的五项 TMDB 配置，改用服务器环境配置或默认值。</p>
      </details>
      <div class="bar">
        <button @click="save(false)">保存配置</button>
        <button class="primary" @click="save(true)">保存并测试 TMDB 连接</button>
      </div>
    </fieldset>
    <p role="status">{{ busy ? '正在处理…' : message }}</p>
  </section>
</template>
<script setup>
import { reactive, ref, watch } from 'vue'
import { api } from '../api.js'
const props = defineProps({ settings: { type: Object, default: null } })
const emit = defineEmits(['saved', 'validated', 'busy'])
const s = ref(null)
const form = reactive({ readToken: '', apiKey: '', proxy: '', language: '', imageBase: '' })
const busy = ref(false)
const message = ref('')
const armClear = ref(false)
let generation = 0
const sourceText = source => ({ db: '设置页', env: '服务器环境配置', default: '系统默认', unset: '未设置' }[source] || '')
function sync(value) {
  s.value = value
  form.proxy = value?.tmdb_proxy || ''
  form.language = value?.tmdb_language || ''
  form.imageBase = value?.tmdb_image_base || ''
}
watch(() => props.settings, sync, { immediate: true })
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
  emit('validated', false)
  try {
    const body = payload()
    if (Object.keys(body).length) {
      const value = await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) })
      form.readToken = ''; form.apiKey = ''
      sync(value); emit('saved', value)
    }
    message.value = '配置已保存'
    if (test) {
      const gen = generation
      const result = await api('/api/tmdb/check', { method: 'POST', timeout: 15000 })
      if (gen !== generation) return
      message.value = result.message + ' · ' + result.elapsed_ms + ' ms'
      emit('validated', result.ok)
    }
  } catch (e) { message.value = '操作失败：' + e.message }
  finally { busy.value = false }
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
    message.value = '已恢复服务器配置或默认值'
  } catch (e) { message.value = '恢复失败：' + e.message }
  finally { busy.value = false }
}
</script>
<style scoped>
.tmdb-settings { line-height: 1.7; }
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
label { display: flex; flex-direction: column; gap: 6px; margin: 12px 0; }
input { width: 100%; box-sizing: border-box; }
details { margin: 16px 0; }
summary { cursor: pointer; }
.hint { color: var(--jz-text-dim); font-size: .875rem; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; }
dd { margin: 0; }
.bar { display: flex; flex-wrap: wrap; gap: 10px; margin: 16px 0; }
a { color: var(--jz-link); }
</style>
