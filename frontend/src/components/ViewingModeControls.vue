<template>
  <div class="nav-viewing">
    <JzButton :disabled="!viewing.ready" :icon="isChildren ? 'eye' : 'lock'" @click="open = true">{{ modeLabel }}</JzButton>
    <JzButton v-if="isPrivate" variant="ghost" icon="logout" icon-only aria-label="退出隐私模式" title="退出隐私模式" @click="lock" :loading="busy" />
  </div>
  <JzDialog v-if="open" title="观看模式" size="small" :busy="busy" @close="open = false">
    <div class="mode-options">
      <JzButton v-for="item in modes" :key="item.value" :aria-pressed="viewing.mode === item.value" :variant="viewing.mode === item.value ? 'primary' : 'secondary'"
        @click="select(item.value)">{{ item.label }}<span>{{ item.description }}</span></JzButton>
    </div>
    <p class="hint">模式仅作用于当前浏览器。儿童模式退出时需要保护密码；隐私模式闲置 15 分钟后锁定，正在播放时除外。</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </JzDialog>
  <JzDialog v-if="viewing.prompt" :title="viewing.prompt.purpose === 'setup' ? '设置保护密码' : viewing.prompt.mode === 'normal' && isChildren ? '退出儿童模式' : '验证保护密码'"
    size="small" layer="auth" :busy="viewing.promptBusy" @close="cancelProtection">
    <form id="protection-prompt" class="protection-form" @submit.prevent="submitProtection(password, confirmation)">
      <p v-if="viewing.prompt.purpose === 'setup'">儿童模式和隐私模式共用此密码，可在“设置 → 访问保护”中修改。</p>
      <p v-else-if="!viewing.prompt.mode">验证后可以管理全部内容，包括隐私内容。当前观看模式保持不变。</p>
      <JzField id="protection-password" label="保护密码" :error="viewing.promptError">
        <input id="protection-password" v-model="password" type="password" maxlength="128" required :minlength="viewing.prompt.purpose === 'setup' ? 6 : undefined"
          :autocomplete="viewing.prompt.purpose === 'setup' ? 'new-password' : 'current-password'" :disabled="viewing.promptBusy" :aria-describedby="viewing.promptError ? 'protection-password-description' : undefined" :aria-invalid="!!viewing.promptError" />
      </JzField>
      <JzField v-if="viewing.prompt.purpose === 'setup'" id="protection-confirmation" label="确认密码" hint="6–128 个字符，请妥善保存。">
        <input id="protection-confirmation" v-model="confirmation" type="password" autocomplete="new-password" minlength="6" maxlength="128" required :disabled="viewing.promptBusy" />
      </JzField>
    </form>
    <template #footer>
      <JzButton :disabled="viewing.promptBusy" @click="cancelProtection">取消</JzButton>
      <JzButton type="submit" form="protection-prompt" variant="primary" :loading="viewing.promptBusy">{{ viewing.prompt.purpose === 'setup' ? '设置并继续' : '确认' }}</JzButton>
    </template>
  </JzDialog>
</template>
<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import JzField from './JzField.vue'
import { viewing, isChildren, isPrivate, modeLabel, changeViewingMode, lockViewing, requestProtection, cancelProtection, submitProtection } from '../viewing.js'
const route = useRoute()
const router = useRouter()
const open = ref(false)
const busy = ref(false)
const error = ref('')
const password = ref('')
const confirmation = ref('')
const modes = [
  { value: 'normal', label: '普通模式', description: '浏览未设为隐私的内容' },
  { value: 'children', label: '儿童模式', description: '仅展示适合儿童且非隐私的内容' },
  { value: 'private', label: '隐私模式', description: '仅展示你手动设为隐私的内容' },
]
watch(() => viewing.prompt, () => { password.value = ''; confirmation.value = '' })
async function leaveManagement() {
  if (!['/settings', '/setup'].includes(route.path)) return true
  return !(await router.push({ path: '/', query: route.query.media ? { media: route.query.media } : {} }))
}
async function select(mode) {
  if (mode === viewing.mode) { open.value = false; return }
  busy.value = true
  error.value = ''
  try {
    if (!(await leaveManagement())) return
    open.value = false
    if (!viewing.enabled || mode === 'private' || isChildren.value) await requestProtection('mode', mode)
    else await changeViewingMode(mode)
  } catch (e) { error.value = e.message; open.value = true }
  finally { busy.value = false }
}
async function lock() {
  busy.value = true
  try { if (await leaveManagement()) await lockViewing() } catch (e) { viewing.notice = e.message }
  finally { busy.value = false }
}
</script>
<style scoped>
.nav-viewing { display: flex; flex-shrink: 0; align-items: center; gap: 4px; white-space: nowrap; }
.mode-options { display: grid; gap: 12px; }
.mode-options :deep(button) { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; white-space: normal; text-align: left; }
.mode-options span { font-size: var(--jz-font-s); font-weight: normal; }
.protection-form { display: grid; gap: 16px; }
.protection-form input { width: 100%; box-sizing: border-box; }
.hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
</style>
