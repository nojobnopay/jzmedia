<template>
  <EmptyState v-if="error" state="error" title="隔离验证未就绪" :text="error" retry @retry="load" />
  <EmptyState v-else-if="!ready" state="loading" title="正在准备设备会话" />
  <App v-else />
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, setToken } from '../../frontend/src/api.js'
import { refreshViewing } from '../../frontend/src/viewing.js'
import App from '../../frontend/src/App.vue'
import EmptyState from '../../frontend/src/components/EmptyState.vue'

const router = useRouter()
const ready = ref(false), error = ref('')
let disposed = false
async function load() {
  error.value = ''; ready.value = false
  try {
    const data = await api('/api/p2/bootstrap')
    if (disposed) return
    setToken(data.api_token)
    await refreshViewing()
    if (!disposed) ready.value = true
  } catch (problem) { if (!disposed) error.value = problem.message }
}
onMounted(() => {
  window.__jzOnlineP2Fixture = { navigate: path => router.push(path), snapshot: () => ({ ready: ready.value, error: error.value, path: router.currentRoute.value.path }) }
  void load()
})
onUnmounted(() => { disposed = true; delete window.__jzOnlineP2Fixture })
</script>
