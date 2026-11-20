<template>
  <Teleport :to="fullscreen || 'body'" :disabled="!origin">
    <SettingsAssistantLauncher v-if="visible" ref="launcher" :open="open" :modal="modal" :raised="!!origin" :busy="busy"
      :section="settingsChat ? settings.dialogProps.section : route.path" :browse="!settingsChat" :tip="bubble" :motion="motion"
      :panel-id="settingsChat ? 'settings-assistant-panel' : 'assistant-page-panel'" :label="settingsChat ? '问设置助手' : '打开小映'"
      @toggle="toggle" @position="corner = $event">
      <AssistantChatDialog v-if="settingsChat" key="management" v-bind="settings.dialogProps" :docked="!modal" :corner="corner"
        @motion="replyMotion = $event" @busy="busy = $event" @close="close" @configure="configure"
        @rules-saved="settings.rulesSaved" @display-saved="settings.displaySaved"
        @update:corner="launcher?.moveToCorner($event)" />
      <AssistantChatDialog v-else :key="scope" v-model:history="history" :channel="channel" :channel-label="channelInfo.label"
        :guide="guide" :viewing-mode="viewingMode" :docked="!modal" :corner="corner"
        @motion="replyMotion = $event" @close="close" @update:corner="launcher?.moveToCorner($event)">
        <template #welcome>
          <ul v-if="tips.length" class="assistant-thread assistant-page-tips" aria-label="本页提示">
            <li v-for="tip in tips" :key="tip.id" class="assistant-welcome">
              <SettingsAssistantReply><p class="assistant-answer">{{ tip.text }}</p></SettingsAssistantReply>
              <div v-if="tip.run" class="assistant-starters">
                <JzButton size="compact" @click="perform(tip)">{{ tip.actionLabel }}<AppIcon name="arrow-right" :size="14" /></JzButton>
              </div>
            </li>
          </ul>
          <SettingsAssistantReply v-else><p class="assistant-answer">我是小映。可以问我这里怎么用，也可以跟我聊几句。</p></SettingsAssistantReply>
          <ul class="assistant-topics assistant-page-guide"><li><HelpLink :page="guide" label="查看本页帮助" icon="help" /></li></ul>
        </template>
        <template #preferences>
          <label class="assistant-footer-control assistant-manual"><input v-model="manual" type="checkbox" />只在点开时提示</label>
        </template>
      </AssistantChatDialog>
    </SettingsAssistantLauncher>
  </Teleport>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { assistantPage } from '../assistantPage.js'
import { createAssistantRoutine } from '../assistantRoutine.js'
import { settingsAssistantHost as settings } from '../settingsAssistantHost.js'
import { activeFocusTraps } from '../useFocusTrap.js'
import SettingsAssistantLauncher from './SettingsAssistantLauncher.vue'
import SettingsAssistantReply from './SettingsAssistantReply.vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import HelpLink from './HelpLink.vue'

const AssistantChatDialog = defineAsyncComponent(() => import('./AssistantChatDialog.vue'))
const props = defineProps({ channel: String, scope: String, blocked: Boolean, viewingMode: String })
const route = useRoute()
const inSettings = computed(() => route.path === '/settings')
const origin = shallowRef(null)
const history = ref([])
const settingsChat = computed(() => !origin.value && inSettings.value && !!settings.value)
const page = computed(() => assistantPage.value?.channel === props.channel ? assistantPage.value : null)
const tips = computed(() => origin.value ? [] : page.value?.tips || [])
const channels = {
  movie: { label: '电影', guide: 'movies' }, tv: { label: '剧集', guide: 'watch-tv' },
  collections: { label: '合集', guide: 'collections' }, online: { label: '在线', guide: 'watch-online' },
  settings: { label: '新手配置', guide: 'onboarding' },
}
const channelInfo = computed(() => inSettings.value ? { label: '设置', guide: 'settings' } : channels[props.channel] || channels.movie)
const guide = computed(() => 'user-guide/' + (route.path === '/' ? 'find-movies' : channelInfo.value.guide))
const open = ref(false), busy = ref(false), replyMotion = ref('idle'), launcher = ref(null), corner = ref('top-right')
const phone = ref(false), reduced = ref(false), hidden = ref(false), fullscreen = shallowRef(null), editing = ref(false)
const modal = computed(() => phone.value || !!origin.value)
const event = shallowRef(null), routine = createAssistantRoutine()
const manualKey = 'jz.assistant.manual', seenKey = 'jz.assistant.seen'
function read(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback } catch { return fallback }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* The preference still applies to this visit. */ }
}
const manual = ref(read(manualKey, false) === true)
const stored = read(seenKey, [])
const seen = new Set(Array.isArray(stored) ? stored.filter(id => typeof id === 'string') : [])
function remember(ids) {
  if (ids.every(id => seen.has(id))) return
  ids.forEach(id => seen.add(id))
  save(seenKey, [...seen])
}
const visible = computed(() => !props.blocked && (!inSettings.value || !!settings.value) &&
  (!!origin.value || (!fullscreen.value && (open.value || !activeFocusTraps.value))))
const automatic = computed(() => visible.value && !page.value?.blocked && !activeFocusTraps.value &&
  !open.value && !hidden.value && !manual.value && !editing.value)
const bubble = computed(() => event.value?.phase === 'tip'
  ? tips.value.find(tip => !tip.manualOnly && tip.id === event.value.id)?.text : '')
const motion = computed(() => {
  if (open.value) return replyMotion.value
  if (!automatic.value || reduced.value || bubble.value) return 'still'
  return event.value?.phase || 'idle'
})

function reset() {
  if (open.value) routine.opened()
  open.value = false
  origin.value = null
  busy.value = false
  replyMotion.value = 'idle'
}
function close() {
  if (busy.value) return
  const trigger = origin.value
  reset()
  nextTick(() => {
    if (trigger?.isConnected) trigger.focus({ preventScroll: true })
    else launcher.value?.focus()
  })
}
function openFrom(trigger) {
  if (props.blocked || busy.value || !trigger?.isConnected) return
  origin.value = trigger
  routine.opened()
  event.value = null
  open.value = true
}
defineExpose({ openFrom })
function toggle() {
  if (open.value) return close()
  routine.opened()
  event.value = null
  remember(tips.value.map(tip => tip.id))
  open.value = true
}
async function configure() {
  const host = settings.value
  if (await host?.configure() && settings.value === host && phone.value) close()
}
async function perform(tip) {
  const location = route.fullPath
  const scope = props.scope
  reset()
  await nextTick()
  if (location === route.fullPath && scope === props.scope && !props.blocked && !page.value?.blocked) {
    tips.value.find(current => current.id === tip.id)?.run?.()
  }
}
watch(manual, value => save(manualKey, value))
watch(() => route.path, () => {
  reset()
  routine.visit()
  event.value = null
})
watch([() => props.scope, () => props.blocked], ([scope, blocked], [previousScope]) => {
  if (blocked || scope !== previousScope) {
    reset()
    history.value = []
    routine.visit()
    event.value = null
  }
}, { flush: 'sync' })
watch(activeFocusTraps, count => {
  if (origin.value) {
    if (!origin.value.isConnected) reset()
  } else if (!inSettings.value && count > (open.value && phone.value ? 1 : 0)) reset()
}, { flush: 'post' })

let timer, phoneQuery, motionQuery
function advance() {
  const current = event.value
  const tip = tips.value.find(item => !item.manualOnly &&
    (current?.phase === 'tip' ? item.id === current.id : !seen.has(item.id)))
  event.value = routine.tick({ tip, motion: !reduced.value })
  if (event.value?.phase === 'tip') remember([event.value.id])
}
function syncTimer(enabled) {
  clearInterval(timer)
  routine.resume()
  event.value = null
  if (enabled) timer = setInterval(advance, 1000)
}
function activity(input) {
  event.value = routine.activity(input?.type === 'pointermove')
}
function focusChanged() {
  editing.value = !!document.activeElement?.matches('input:not([type=button]), textarea, select, [contenteditable="true"]')
}
function syncDisplay() {
  hidden.value = document.hidden
  fullscreen.value = document.fullscreenElement
  phone.value = !!phoneQuery?.matches
  reduced.value = !!motionQuery?.matches
  if (reduced.value && event.value?.phase?.startsWith('ambient-')) activity()
}
const inputs = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'scroll']
let stopTimer
onMounted(() => {
  phoneQuery = window.matchMedia?.('(max-width: 700px)')
  motionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)')
  syncDisplay()
  focusChanged()
  phoneQuery?.addEventListener('change', syncDisplay)
  motionQuery?.addEventListener('change', syncDisplay)
  document.addEventListener('visibilitychange', syncDisplay)
  document.addEventListener('fullscreenchange', syncDisplay)
  document.addEventListener('focusin', focusChanged)
  document.addEventListener('focusout', deferFocus)
  for (const name of inputs) window.addEventListener(name, activity, { capture: true, passive: true })
  stopTimer = watch(automatic, syncTimer, { immediate: true, flush: 'sync' })
})
function deferFocus() { nextTick(focusChanged) }
onBeforeUnmount(() => {
  stopTimer?.()
  clearInterval(timer)
  phoneQuery?.removeEventListener('change', syncDisplay)
  motionQuery?.removeEventListener('change', syncDisplay)
  document.removeEventListener('visibilitychange', syncDisplay)
  document.removeEventListener('fullscreenchange', syncDisplay)
  document.removeEventListener('focusin', focusChanged)
  document.removeEventListener('focusout', deferFocus)
  for (const name of inputs) window.removeEventListener(name, activity, true)
})
</script>

<style scoped>
.assistant-page-tips { list-style: none; margin: 0; padding: 0; }
.assistant-page-guide:only-child { margin: 0; padding: 0; border: 0; }
.assistant-manual { gap: 9px; padding: 5px 14px; color: var(--jz-text-dim); min-height: 38px; cursor: pointer; }
.assistant-manual input { margin: 0; }
@media (max-width: 700px), (pointer: coarse) {
  .assistant-manual { min-height: 44px; }
}
</style>
