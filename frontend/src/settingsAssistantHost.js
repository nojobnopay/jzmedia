import { onBeforeUnmount, onMounted, shallowRef } from 'vue'

export const settingsAssistantHost = shallowRef(null)

export function useSettingsAssistantHost(host) {
  onMounted(() => { settingsAssistantHost.value = host })
  onBeforeUnmount(() => {
    if (settingsAssistantHost.value === host) settingsAssistantHost.value = null
  })
}
