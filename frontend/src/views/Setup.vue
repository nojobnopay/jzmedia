<template>
  <main ref="pageRef" class="setup-page">
    <header class="setup-heading">
      <div><p class="eyebrow">新手配置<span v-if="state">第 {{ state.step }} / {{ SETUP_STEPS.length }} 步</span></p><h1>让第一部内容进入媒体库</h1><p class="setup-description">确认文件位置，扫描或上传。配置进度会自动保存。</p></div>
      <router-link v-if="state?.status === 'completed'" :to="wallLink">返回媒体库</router-link>
      <button v-else @click="defer" :disabled="blocked || !state">稍后继续</button>
    </header>
    <ol class="setup-steps" aria-label="配置步骤">
      <li v-for="(label, index) in SETUP_STEPS" :key="label" :class="{ current: state?.step === index + 1, done: state?.step > index + 1 || state?.status === 'completed' }">
        <button :aria-current="state?.step === index + 1 ? 'step' : undefined"
          :disabled="blocked || scan.running.value || !state || index + 1 > state.step"
          @click="go(index + 1)"><span aria-hidden="true"><AppIcon v-if="state?.step > index + 1 || state?.status === 'completed'" name="check" :size="16" /><template v-else>{{ index + 1 }}</template></span>{{ label }}</button>
      </li>
    </ol>
    <p v-if="loadError || error" class="setup-error" role="alert">{{ loadError || error }} <button @click="load" :disabled="blocked">重试加载</button></p>
    <p v-if="loading" role="status">正在恢复配置进度…</p>
    <template v-else-if="state">
      <section v-if="state.step === 1" class="setup-card">
        <TmdbSettingsPanel :settings="settings" @saved="settings = $event" @validated="tmdbReady = $event" @busy="childBusy = $event" />
        <p v-if="state.tmdb_verified && tmdbReady" class="hint">当前配置已验证，可继续设置视频库。</p>
        <p v-if="state.tmdb_skipped" class="hint">已选择稍后配置 TMDB；本地资料和已启用的备用来源仍可使用。</p>
        <div class="setup-actions setup-next">
          <button @click="skipTmdb" :disabled="blocked">稍后配置 TMDB</button>
          <button class="primary" @click="nextSource" :disabled="blocked || !tmdbReady">下一步：设置视频库</button>
        </div>
      </section>
      <section v-else-if="state.step === 2" class="setup-card">
        <SetupLibraryStep :library-id="state.library_id" :initial-kind="state.kind" :accept="acceptLibrary"
          @busy="childBusy = $event" @changed="syncLibraries" />
      </section>
      <section v-else-if="state.step === 3" class="setup-card">
        <h2>添加首部{{ state.kind === 'tv' ? '剧集' : '电影' }}</h2>
        <p class="setup-target">目标视频库<strong>{{ target?.media_name }} / {{ target?.name }}</strong></p>
        <div class="setup-choices" role="group" aria-label="添加内容的方式">
          <label :class="{ chosen: state.import_mode === 'scan' }"><input type="radio" name="import-mode" :checked="state.import_mode === 'scan'" @change="setMode('scan')" :disabled="blocked || scan.running.value" /><span>扫描服务器已有文件<small>文件已在 NAS 或服务器上</small></span></label>
          <label :class="{ chosen: state.import_mode === 'upload', unavailable: !state.upload_allowed }"><input type="radio" name="import-mode" :checked="state.import_mode === 'upload'" @change="setMode('upload')" :disabled="blocked || scan.running.value || !state.upload_allowed" /><span>从当前设备上传<small>把电脑或手机中的文件添加到库中</small></span></label>
        </div>
        <p v-if="!state.upload_allowed" class="hint">此库可扫描和播放；当前未确认写入权限，上传需要可写的视频库。</p>
        <template v-if="state.import_mode === 'scan'">
          <p>扫描此视频库的已有文件，并尝试补全资料。首次可先放入一部电影或一集剧集，确认识别结果。</p>
          <p class="hint">扫描可能按库设置写入 NFO 或海报；不会自动执行目录整理。</p>
          <div class="setup-actions"><button class="primary" @click="scan.start" :disabled="blocked || scan.blocked.value">扫描此视频库</button><button v-if="scan.running.value" @click="scan.cancel">取消扫描</button></div>
        </template>
        <template v-else>
          <p>{{ state.kind === 'tv' ? '选择包含剧名的完整文件夹；散集文件需指定所属剧和季号。' : '选择电影文件或包含影片、字幕的文件夹。' }}同名文件跳过，不会覆盖。</p>
          <button class="primary" @click="uploadOpen = true" :disabled="blocked || !target || !state.upload_allowed || scan.blocked.value">选择并上传文件</button>
          <p class="hint">上传期间请保持页面打开；刷新后需重新选择文件。</p>
        </template>
        <p role="status">{{ scan.message.value }}</p>
        <p v-if="lastUpload" role="status">{{ uploadSummary(lastUpload) }}</p>
        <div class="setup-result-count">
          <strong>此库已登记 {{ state.content.count }} {{ state.kind === 'tv' ? '个分集文件' : '个电影文件' }}</strong>
          <p v-if="!state.content.count">还没有可展示的内容。仅上传字幕、花絮或扫描到空目录不会完成本步骤。</p>
          <p v-else-if="state.content.pending">{{ state.content.pending }} 项资料或匹配待处理，入库结果中可继续核对。</p>
          <button @click="refresh" :disabled="blocked">刷新入库结果</button>
        </div>
        <div class="setup-actions setup-next">
          <button @click="defer" :disabled="blocked">稍后导入</button>
          <button class="primary" @click="go(4)" :disabled="blocked || scan.running.value || !state.content.count">查看入库结果</button>
        </div>
      </section>
      <section v-else class="setup-card">
        <div class="setup-complete-heading"><span class="setup-complete-mark"><AppIcon name="check" :size="26" /></span><div><h2>{{ state.status === 'completed' ? '基本配置已完成' : '内容已入库，请核对结果' }}</h2><p>{{ target?.media_name }} / {{ target?.name }}：已登记 {{ state.content.count }} {{ state.kind === 'tv' ? '个分集文件' : '个电影文件' }}。</p></div></div>
        <p v-if="state.tmdb_skipped" class="hint">TMDB 已跳过，可在“设置 → 在线资料服务”中补配。</p>
        <p v-if="lastUpload" role="status">{{ uploadSummary(lastUpload) }}</p>
        <p role="status">{{ scan.message.value }}</p>
        <p v-if="state.content.pending" class="setup-pending">{{ state.content.pending }} 项资料或匹配待处理。已入库不代表资料已匹配，请进入详情核对。</p>
        <ul class="setup-items">
          <li v-for="item in state.content.items" :key="item.id">
            <router-link :to="itemLink(item, state.kind)"><strong>{{ state.kind === 'tv' ? item.show_title + ' · ' : '' }}{{ item.title || (state.kind === 'tv' ? '第 ' + item.episode + ' 集' : '查看影片') }}</strong><small>查看详情与播放</small></router-link>
            <span v-if="item.pending">待匹配／待确认</span>
          </li>
        </ul>
        <p class="hint">这里显示最近登记的最多 5 项。目录整理可稍后在库工具中预览、确认。</p>
        <div class="setup-actions setup-related">
          <router-link :to="pendingLink">处理匹配待办</router-link>
          <router-link :to="toolsLink">目录整理与库工具</router-link>
        </div>
        <div class="setup-actions setup-next">
          <button v-if="state.status !== 'completed'" class="primary" @click="complete" :disabled="blocked || !state.can_complete">完成引导</button>
          <router-link :to="wallLink">前往{{ state.kind === 'tv' ? '剧集' : '电影' }}库</router-link>
          <button v-if="state.status === 'completed'" @click="addAnother" :disabled="blocked">继续添加另一视频库</button>
        </div>
      </section>
      <footer class="setup-actions">
        <button v-if="state.step > 1 && state.status !== 'completed'" @click="go(state.step - 1)" :disabled="blocked || scan.running.value">上一步</button>
        <span class="hint">稍后继续会保留已保存的配置。</span><HelpLink page="user-guide/onboarding" label="配置帮助" />
      </footer>
    </template>
    <UploadDialog v-if="uploadOpen && target" :kind="state.kind" :library-id="target.id"
      @busy="uploadBusy = $event" @result="uploaded" @done="refresh"
      @close="uploadOpen = false; uploadBusy = false; refresh()" />
  </main>
</template>
<script setup>
import HelpLink from '../components/HelpLink.vue'
import AppIcon from '../components/AppIcon.vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { api } from '../api.js'
import { listLibs, loadLibs } from '../libraries.js'
import { SETUP_STEPS, itemLink, uploadSummary } from '../onboarding.js'
import { useOnboarding } from '../useOnboarding.js'
import { useLibraryScan } from '../useLibraryScan.js'
import TmdbSettingsPanel from '../components/TmdbSettingsPanel.vue'
import SetupLibraryStep from '../components/SetupLibraryStep.vue'
import UploadDialog from '../components/UploadDialog.vue'
import '../styles/setup.css'

const router = useRouter()
const pageRef = ref(null)
const { state, busy, error, refresh, save } = useOnboarding()
const loading = ref(false)
const loadError = ref('')
const settings = ref(null)
const libs = ref(listLibs())
const childBusy = ref(false)
const uploadBusy = ref(false)
const uploadOpen = ref(false)
const tmdbReady = ref(false)
const target = computed(() => libs.value.find(l => l.id === state.value?.library_id))
const blocked = computed(() => loading.value || busy.value || childBusy.value || uploadBusy.value)
const scan = useLibraryScan(() => target.value, refresh)
const lastUpload = computed(() => {
  const result = state.value?.upload_result
  return result && result.library_id === state.value?.library_id ? result : null
})
const wallLink = computed(() => ({ path: state.value?.kind === 'tv' ? '/tv' : '/', query: target.value ? { media: target.value.media_library_id } : {} }))
const pendingLink = computed(() => ({ path: '/settings', query: { sec: 'sec-pending', library: state.value?.library_id } }))
const toolsLink = computed(() => ({ path: '/settings', query: { sec: 'sec-libtools', library: state.value?.library_id } }))
watch(() => state.value?.step, async (step, previous) => {
  childBusy.value = false
  if (previous == null || step === previous) return
  await nextTick()
  const heading = pageRef.value?.querySelector('.setup-card h2, .setup-card h3')
  heading?.setAttribute('tabindex', '-1')
  heading?.focus({ preventScroll: true })
  pageRef.value?.scrollIntoView({ block: 'start' })
})
function syncLibraries() { libs.value = listLibs() }
async function load() {
  if (blocked.value) return
  loading.value = true; loadError.value = ''
  try {
    const [, config] = await Promise.all([loadLibs(api, { force: true }), api('/api/settings')])
    settings.value = config; syncLibraries()
    const data = await refresh()
    if (data) {
      tmdbReady.value = data.tmdb_verified
      if (data.status === 'not_started' || data.status === 'deferred') await save({ status: 'active' })
    }
  } catch (e) { loadError.value = '配置加载失败：' + e.message }
  finally { loading.value = false }
}
async function go(step) {
  if (blocked.value) return
  await save({ status: 'active', step })
}
async function skipTmdb() {
  if (!blocked.value) await save({ status: 'active', tmdb_skipped: true, step: 2 })
}
async function nextSource() {
  if (!blocked.value && tmdbReady.value) await save({ status: 'active', tmdb_skipped: false, step: 2 })
}
async function acceptLibrary(lib, check) {
  const result = await save({ status: 'active', library_id: lib.id, kind: lib.kind, step: 3,
    import_mode: lib.read_only || !check.writable ? 'scan' : state.value.import_mode })
  if (!result) throw new Error(error.value || '进度未保存，请重试')
}
async function setMode(import_mode) { if (!blocked.value) await save({ import_mode }) }
async function uploaded(result) {
  const { library_id, uploaded, skipped, failed, cancelled } = result
  await save({ upload_result: { library_id, uploaded, skipped, failed, cancelled } })
}
async function defer() {
  if (blocked.value) return
  if (await save({ status: 'deferred' })) await router.push(wallLink.value)
}
async function complete() {
  if (!blocked.value) await save({ status: 'completed', step: 4 })
}
async function addAnother() {
  if (!blocked.value) await save({ status: 'active', step: 2, library_id: null, upload_result: null })
}
onBeforeRouteLeave(() => {
  if (blocked.value) { loadError.value = '请等待当前保存完成，或先取消上传，再离开向导。'; return false }
})
onMounted(load)
</script>
