<template>
  <fieldset class="online-migration" data-online-host-migration :disabled="disabled">
    <legend>确认允许列表的主机替换</legend>
    <p class="hint">保存时将勾选的旧主机替换为对应的新主机。</p>
    <p v-if="changes.some(change => change.kind === 'api')" class="hint">新的接口地址始终生效。取消勾选旧接口主机后，它仍保留在允许列表，可继续用于媒体或海报。</p>
    <ul class="online-host-list">
      <li v-for="change in changes" :key="change.old_host">
        <label><input v-model="selected" type="checkbox" :value="change.old_host" /><span>{{ migrationKinds[change.kind] }} · {{ change.old_host }} 换为 {{ change.new_host }}</span></label>
        <a v-if="safeHomepage(change.notice_url)" :href="safeHomepage(change.notice_url)" target="_blank" rel="noopener noreferrer" :aria-label="change.old_host + ' 的来源公告'">来源公告</a>
      </li>
    </ul>
  </fieldset>
</template>

<script setup>
import { migrationKinds, safeHomepage } from '../useOnlineHostMigration.js'

defineProps({ changes: { type: Array, required: true }, disabled: Boolean })
const selected = defineModel({ type: Array, required: true })
</script>
