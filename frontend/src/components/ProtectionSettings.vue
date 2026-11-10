<template>
  <section class="protection-settings">
    <div class="section-heading"><h3>儿童与隐私保护</h3><span class="status-label">{{ viewing.enabled ? '已设置密码' : '未设置密码' }}</span></div>
    <p class="hint">全部媒体库共用一个保护密码。各设备独立选择观看模式；退出儿童模式、进入隐私模式和管理受保护内容时需要验证。</p>
    <form class="settings-form" @submit.prevent="save">
      <JzField v-if="viewing.enabled" id="protection-current" label="当前保护密码"><input id="protection-current" v-model="current" type="password" autocomplete="current-password" required :disabled="busy" /></JzField>
      <JzField id="protection-new" :label="viewing.enabled ? '新保护密码' : '保护密码'" hint="6–128 个字符"><input id="protection-new" v-model="password" type="password" autocomplete="new-password" minlength="6" maxlength="128" required :disabled="busy" /></JzField>
      <JzField id="protection-repeat" label="确认密码" :error="mismatch ? '两次输入的密码不一致' : ''" v-slot="field"><input id="protection-repeat" v-model="confirmation" :aria-describedby="field.describedby" :aria-invalid="field.invalid || undefined" @input="mismatch = false" type="password" autocomplete="new-password" minlength="6" maxlength="128" required :disabled="busy" /></JzField>
      <p class="hint">修改后，所有设备的隐私和管理授权会锁定，儿童模式保持不变。</p>
      <SettingsFormActions :dirty="dirty" :busy="busy || locking" :status="message ? '密码未保存，请查看错误提示' : ''" label="保护密码操作">
        <JzButton type="submit" variant="primary" :loading="busy" :disabled="locking">{{ viewing.enabled ? '修改保护密码' : '设置保护密码' }}</JzButton>
        <JzButton v-if="dirty" :disabled="busy || locking" @click="discard">放弃修改</JzButton>
        <JzButton v-else-if="viewing.management_unlocked" :disabled="busy" :loading="locking" @click="lock">锁定管理权限</JzButton>
      </SettingsFormActions>
      <p v-if="message" role="status">{{ message }}</p>
    </form>
  </section>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import SettingsFormActions from './SettingsFormActions.vue'
import { viewing, saveProtectionPassword, lockViewing } from '../viewing.js'
import { useSettingsDraft } from '../settingsDrafts.js'
const current = ref('')
const password = ref('')
const confirmation = ref('')
const busy = ref(false)
const locking = ref(false)
const message = ref(''), mismatch = ref(false)
const dirty = computed(() => !!(current.value || password.value || confirmation.value))
function discard() { if (busy.value || locking.value) return; current.value = ''; password.value = ''; confirmation.value = ''; message.value = ''; mismatch.value = false }
const route = useRoute()
const router = useRouter()
useSettingsDraft({ section: 'sec-auth', label: '保护密码', dirty: () => dirty.value, busy: () => busy.value, discard })
async function lock() {
  locking.value = true
  try {
    if (!(await router.push({ path: '/', query: route.query.media ? { media: route.query.media } : {} }))) await lockViewing()
  } catch (e) { viewing.notice = e.message }
  finally { locking.value = false }
}
async function save() {
  if (busy.value || locking.value) return
  message.value = ''; mismatch.value = false
  if (password.value !== confirmation.value) { mismatch.value = true; document.getElementById('protection-repeat')?.focus(); return }
  busy.value = true
  try {
    await saveProtectionPassword(current.value, password.value)
    current.value = ''; password.value = ''; confirmation.value = ''
    viewing.notice = '保护密码已保存，请使用新密码重新验证。'
  } catch (e) { message.value = e.message }
  finally { busy.value = false }
}
</script>
<style scoped>
.protection-settings { border-bottom: 1px solid var(--jz-border); padding-bottom: 24px; margin-bottom: 24px; }
.protection-settings input { width: 100%; box-sizing: border-box; }
</style>
