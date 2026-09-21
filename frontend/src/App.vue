<template>
  <nav>
    <router-link to="/" class="brand"><img src="/favicon.svg" alt="jzmedia" width="26" height="26" /><span>jzmedia</span></router-link>
    <router-link to="/">电影</router-link>
    <router-link to="/tv">剧集</router-link>
    <router-link to="/collections">合集</router-link>
    <router-link to="/settings">设置</router-link>
    <span class="nav-spacer"></span>
    <select v-if="showSwitch" class="lib-switch" :value="currentId" title="切换媒体库"
      @change="onSwitch">
      <option v-for="m in libs" :key="m.id" :value="m.id">{{ optionLabel(m) }}</option>
    </select>
  </nav>
  <router-view />
  <div v-if="authAsk" class="auth-mask">
    <div class="auth-dlg">
      <h3>需要访问令牌</h3>
      <p class="auth-hint">服务端已启用写操作鉴权：粘贴访问令牌（.env 的 <code>JZMEDIA_TOKEN</code> 或设置页里设的值）。电视/Kodi 直链读取不受影响。</p>
      <input v-model="authInput" type="password" placeholder="访问令牌" autocomplete="off"
        @keyup.enter="saveAuth" />
      <div class="auth-bar">
        <button @click="saveAuth" :disabled="!authInput.trim()">保存并重试</button>
        <button @click="authAsk = false">取消</button>
      </div>
      <p class="auth-hint">保存后仅存于本浏览器 localStorage，不会上传。</p>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { setToken, api } from './api.js'
import { listMediaLibs, currentMediaId, loadLibs, switchMedia } from './libraries.js'

const authAsk = ref(false)
const authInput = ref('')
const libs = ref([])          // 媒体库列表
const currentId = ref(null)   // 当前媒体库 id
const router = useRouter()
const route = useRoute()

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
  // 换媒体库：海报墙/剧集页原地刷新；详情/人物页的 id 属于旧库，回首页
  switchMedia(Number(e.target.value))
  if (route.path !== '/' && route.path !== '/tv') router.push('/')
}
onMounted(async () => {
  try {
    await loadLibs(api)
    libs.value = listMediaLibs()
    currentId.value = currentMediaId()
  } catch (e) { /* 库接口不可用时保持旧行为（后端报错会在页面自现） */ }
  window.addEventListener('jzmedia:unauthorized', onUnauthorized)
  window.addEventListener('jzmedia:libraries-changed', syncLibs)
})
onUnmounted(() => {
  window.removeEventListener('jzmedia:unauthorized', onUnauthorized)
  window.removeEventListener('jzmedia:libraries-changed', syncLibs)
})
function syncLibs() {
  libs.value = listMediaLibs()
  currentId.value = currentMediaId()
}
</script>
<style>
body { font-family: system-ui, sans-serif; margin: 0; background: #141414; color: #eee; }
nav { padding: 12px; background: #1f1f1f; display: flex; gap: 16px; align-items: center; }
nav a { color: #eee; text-decoration: none; }
nav a.router-link-active { color: #e50914; font-weight: bold; }
nav .brand { display: inline-flex; align-items: center; gap: 8px; font-weight: 800; letter-spacing: 0.5px; }
nav .brand img { border-radius: 6px; display: block; }
nav .brand.router-link-active { color: #eee; font-weight: 800; }
.nav-spacer { flex: 1; }
.lib-switch { background: #222; color: #eee; border: 1px solid #444; border-radius: 6px; padding: 6px 10px; font-size: 0.875rem; max-width: 260px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--poster-min, 150px), 1fr)); gap: 12px; padding: 12px; }
.card { background: #222; border-radius: 8px; overflow: hidden; cursor: pointer; }
.card img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.card .t { padding: 8px; font-size: 0.875rem; }
/* 共享页面块（评审 R07-Q3 样式单源）：Detail/Person 等页面卡片与头部；
   页面可在 scoped 样式里覆盖特化属性（如 Settings 的 margin-bottom、Person 的 .hero-inner 宽度） */
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.hero-main { display: flex; gap: 20px; margin-top: 12px; align-items: flex-start; }
.hero-info { min-width: 0; }
.hero-info h2 { margin: 0 0 8px; font-size: 1.875rem; }
.meta-line { color: #aaa; font-size: 1rem; margin: 8px 0; }
.overview { margin: 0; line-height: 1.8; color: #e6e6e6; font-size: 1rem; white-space: pre-wrap; }
.empty { margin: 0; color: #777; font-size: 0.9375rem; }
.cast-name { font-size: 0.875rem; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cast-char { font-size: 0.75rem; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar { padding: 12px; display: flex; gap: 8px; }
input, button, textarea { font-size: 0.875rem; padding: 6px 10px; border-radius: 6px; border: 1px solid #444; background: #222; color: #eee; }
input[type="range"] { padding: 0; }
button { cursor: pointer; }
.page { padding: 12px; max-width: 900px; }
.actor { cursor: pointer; color: #6ab0ff; }
.poster-wrap { position: relative; }
.poster-wrap img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.score-badge { position: absolute; top: 6px; right: 6px; font-size: 0.75rem; font-weight: bold; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.72); color: #ffc107; white-space: nowrap; }
.score-badge.douban { color: #7ed321; }
.score-badge.custom { color: #ff6b6b; }
.stars { color: #ffc107; letter-spacing: 1px; }
.rate-chip { display: inline-block; font-size: 0.9375rem; padding: 4px 14px; border-radius: 999px; border: 1px solid #444; margin-right: 6px; }
.rate-chip.tmdb { color: #ffc107; border-color: #6b5518; }
.rate-chip.douban { color: #7ed321; border-color: #3a5a1e; }
.rate-chip.custom { color: #ff6b6b; border-color: #6e2b2b; }
.rating-row { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.auth-mask { position: fixed; inset: 0; background: rgba(0,0,0,.66); display: flex; align-items: center; justify-content: center; z-index: 90; }
.auth-dlg { background: #1c1c1c; border: 1px solid #444; border-radius: 10px; padding: 16px; width: min(420px, calc(100vw - 32px)); display: flex; flex-direction: column; gap: 10px; }
.auth-dlg h3 { margin: 0; }
.auth-dlg input { width: 100%; box-sizing: border-box; }
.auth-bar { display: flex; gap: 8px; }
.auth-hint { margin: 0; color: #888; font-size: 0.8125rem; line-height: 1.6; }
.auth-hint code { color: #9ecfff; }
</style>
