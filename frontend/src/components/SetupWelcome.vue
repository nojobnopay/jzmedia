<template>
  <aside v-if="state?.show_welcome" class="setup-welcome" aria-label="新手配置">
    <div><strong>{{ state.status === 'active' ? '继续完成基本配置' : '欢迎使用 jzmedia' }}</strong>
      <p>跟随四个步骤配置资料来源、视频库，并扫描或上传第一部内容。</p></div>
    <router-link to="/setup">{{ state.status === 'active' ? '继续配置' : '开始配置' }}</router-link>
    <JzButton variant="ghost" @click="dismiss" :disabled="busy" type="button">暂不需要</JzButton>
    <p v-if="error" role="alert">{{ error }}</p>
  </aside>
</template>
<script setup>
import JzButton from './JzButton.vue'

import { onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useOnboarding } from '../useOnboarding.js'
const route = useRoute()
const { state, busy, error, refresh, save } = useOnboarding()
function dismiss() { return save({ status: 'deferred' }) }
watch(() => route.fullPath, refresh)
onMounted(() => {
  refresh()
  window.addEventListener('focus', refresh)
  window.addEventListener('jzmedia:libraries-changed', refresh)
  window.addEventListener('jzmedia:content-changed', refresh)
})
onUnmounted(() => {
  window.removeEventListener('focus', refresh)
  window.removeEventListener('jzmedia:libraries-changed', refresh)
  window.removeEventListener('jzmedia:content-changed', refresh)
})
</script>
<style scoped>
.setup-welcome { max-width: 1300px; margin: 20px auto 0; padding: 16px 20px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); background: var(--jz-surface); }
.setup-welcome div { flex: 1; min-width: 220px; }
p { margin: 4px 0 0; color: var(--jz-text-dim); font-size: .875rem; }
a { color: var(--jz-on-accent); background: var(--jz-accent); border-radius: var(--jz-radius-s); font-weight: 600; text-decoration: none; padding: 10px 14px; }
@media (max-width: 700px) { .setup-welcome { margin: 12px; padding: 16px; } a, button { min-height: var(--jz-touch-target); box-sizing: border-box; } }
</style>
