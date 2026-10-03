<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <nav class="app-nav" aria-label="主导航">
    <router-link :to="navTarget('/')" custom v-slot="{ href, navigate }">
      <a :href="href" class="brand" aria-label="jzmedia 电影" @click="navigate"><img src="/favicon.svg" alt="" width="26" height="26" /><span>jzmedia</span></a>
    </router-link>
    <div class="nav-channels">
      <router-link v-for="item in channels" :key="item.key" :to="navTarget(item.path)" custom v-slot="{ href, navigate }">
        <a :href="href" class="nav-link" :class="{ 'is-current': activeSection === item.key }"
          :aria-current="activeSection === item.key ? 'page' : undefined" @click="navigate">
          <AppIcon :name="item.icon" /><span>{{ item.label }}</span>
        </a>
      </router-link>
    </div>
    <div class="nav-tools">
      <router-link :to="navTarget('/settings')" custom v-slot="{ href, navigate }">
        <a :href="href" class="nav-link" :class="{ 'is-current': activeSection === 'settings' }"
          :aria-current="activeSection === 'settings' ? 'page' : undefined" @click="navigate">
          <AppIcon name="settings" /><span>设置</span>
        </a>
      </router-link>
      <HelpLink label="帮助" icon="help" class="nav-link" />
      <label v-if="showSwitch" class="nav-library" title="切换媒体库">
        <AppIcon name="library" />
        <select class="lib-switch" :value="currentId" aria-label="切换媒体库" @change="onSwitch">
          <option v-for="m in libs" :key="m.id" :value="m.id">{{ optionLabel(m) }}</option>
        </select>
      </label>
    </div>
  </nav>
  <div id="main-content" class="app-content" tabindex="-1">
    <SetupWelcome v-if="route.path === '/' || route.path === '/tv'" />
    <router-view />
  </div>
  <BackToTop />
  <JzDialog v-if="authAsk" title="需要访问令牌" size="small" layer="auth" class="auth-dlg" mask-class="auth-mask" @close="authAsk = false">
    <p class="auth-hint">请输入此服务设置的访问令牌，继续当前操作。</p>
    <label for="access-token">访问令牌
      <input id="access-token" v-model="authInput" name="access-token" type="password" placeholder="粘贴访问令牌…" autocomplete="off" :spellcheck="false" @keyup.enter="saveAuth" />
    </label>
    <p class="auth-hint">令牌仅保存在当前浏览器，用于向此服务验证身份。</p>
    <template #footer>
      <JzButton @click="authAsk = false">取消</JzButton>
      <JzButton variant="primary" @click="saveAuth" :disabled="!authInput.trim()">保存并重试</JzButton>
    </template>
  </JzDialog>
</template>
<script setup>
import HelpLink from './components/HelpLink.vue'
import SetupWelcome from './components/SetupWelcome.vue'
import BackToTop from './components/BackToTop.vue'
import JzDialog from './components/JzDialog.vue'
import JzButton from './components/JzButton.vue'
import AppIcon from './components/AppIcon.vue'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { setToken, api } from './api.js'
import { listMediaLibs, currentMediaId, loadLibs, onLibChange, switchMedia } from './libraries.js'

const authAsk = ref(false)
const authInput = ref('')
const libs = ref([])          // 媒体库列表
const currentId = ref(null)   // 当前媒体库 id
const router = useRouter()
const route = useRoute()
let offLibChange = null
const channels = [
  { key: 'movie', path: '/', label: '电影', icon: 'movie' },
  { key: 'tv', path: '/tv', label: '剧集', icon: 'tv' },
  { key: 'collections', path: '/collections', label: '合集', icon: 'collections' },
]
const activeSection = computed(() => {
  const path = route.path
  if (path === '/tv' || path.startsWith('/tv/')) return 'tv'
  if (path === '/collections' || path.startsWith('/c/')) return 'collections'
  if (path === '/settings' || path === '/setup') return 'settings'
  if (path === '/' || path.startsWith('/m/') || path.startsWith('/p/')) return 'movie'
  return ''
})
function navTarget(path) {
  return currentId.value ? { path, query: { media: currentId.value } } : { path }
}

const showSwitch = computed(() => libs.value.length > 1)
function optionLabel(m) {
  return m.name + (m.read_only ? ' · 只读' : '')
}

function onUnauthorized() {
  if (authAsk.value) return
  authInput.value = ''
  authAsk.value = true
}
function saveAuth() {
  const t = authInput.value.trim()
  if (!t) return
  setToken(t)
  authAsk.value = false
  location.reload()   // 简单可靠：带令牌重载，正在失败的请求由页面自行重试
}
function onSwitch(e) {
  const id = Number(e.target.value)
  const path = route.path.startsWith('/tv') ? '/tv'
    : route.path.startsWith('/c/') || route.path === '/collections' ? '/collections'
      : route.path === '/setup' ? '/setup' : route.path === '/settings' ? '/settings' : '/'
  // URL 先更新，避免旧 query 的 media 参数把选择切回旧库。
  router.push({ path, query: path === '/settings' ? { sec: 'sec-libtools', media: id } : { media: id } })
    .then(failure => {
      // A file-review guard may redirect back to scan the original library.
      if (!failure && Number(router.currentRoute.value.query.media) === id) switchMedia(id)
      else e.target.value = currentId.value
    })
}
onMounted(async () => {
  offLibChange = onLibChange(syncLibs)
  try {
    await loadLibs(api)
    libs.value = listMediaLibs()
    currentId.value = currentMediaId()
  } catch (e) { /* 库接口不可用时保持旧行为（后端报错会在页面自现） */ }
  window.addEventListener('jzmedia:unauthorized', onUnauthorized)
  window.addEventListener('jzmedia:libraries-changed', syncLibs)
})
onUnmounted(() => {
  if (offLibChange) offLibChange()
  window.removeEventListener('jzmedia:unauthorized', onUnauthorized)
  window.removeEventListener('jzmedia:libraries-changed', syncLibs)
})
function syncLibs() {
  libs.value = listMediaLibs()
  currentId.value = currentMediaId()
}
</script>
