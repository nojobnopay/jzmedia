<template>
  <JzDialog id="settings-assistant-panel" ref="panel" title="设置助手" close-label="关闭设置助手"
    :presentation="docked ? 'inline' : 'default'" teleport-disabled mask-class="assistant-chat-mask"
    :busy="applying" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="assistant-heading">
        <div class="assistant-title">
          <h2 :id="titleId">设置助手</h2>
          <span v-if="pages.get(section)" :title="pages.get(section)">{{ pages.get(section) }}</span>
        </div>
        <details ref="privacy" class="assistant-privacy" @keydown.esc="dismissPrivacy" @focusout="leavePrivacy">
          <summary title="对话隐私" aria-label="对话隐私"><AppIcon name="info" :size="17" /></summary>
          <p id="settings-question-privacy">提问会将问题、近期对话、当前分区和相关手册摘录发送给已配置的模型。密钥、令牌和媒体路径请在原设置表单填写。</p>
        </details>
        <label class="assistant-dock" title="停靠位置">
          <AppIcon name="grid" :size="17" />
          <select aria-label="停靠位置" :value="corner" @change="emit('update:corner', $event.target.value)">
            <option v-if="!corner" value="" disabled>自定义</option>
            <option value="top-left">左上角</option><option value="top-right">右上角</option>
            <option value="bottom-left">左下角</option><option value="bottom-right">右下角</option>
          </select>
        </label>
      </div>
    </template>
    <div ref="thread" class="assistant-thread" aria-label="聊天记录">
      <div v-if="!turns.length && !transient && readiness === 'ready'" class="assistant-welcome">
        <SettingsAssistantReply>
          <p class="assistant-answer">想设置什么？我可以帮你找步骤，也可以一起调整设置。</p>
        </SettingsAssistantReply>
        <div class="assistant-starters" role="group" aria-label="填入示例问题">
          <JzButton v-for="example in examples" :key="example" size="compact" @click="prefill(example)">
            {{ example }}<AppIcon name="arrow-right" :size="14" />
          </JzButton>
        </div>
      </div>
      <div v-for="(turn, i) in conversation" :key="turn.id" class="assistant-turn"
        :class="{ 'assistant-pending': turn === transient && asking }">
        <component :is="turn === transient ? 'p' : 'h3'" :ref="el => keepLatest(el, i)"
          class="assistant-question" tabindex="-1">{{ turn.question }}</component>
        <SettingsAssistantReply>
          <div v-if="turn === transient && asking" class="assistant-typing" role="status">
            <span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>
            <span class="assistant-sr">正在查找配置方法…</span>
          </div>
          <div v-else-if="turn.error" role="alert" class="assistant-failure">
            <p>{{ turn.error }}</p><HelpLink page="user-guide/settings" label="打开设置手册" />
          </div>
          <template v-else>
            <p class="assistant-answer">{{ turn.answer }}</p>
            <small v-if="turn.mode === 'local'" class="assistant-source">回答来自本版手册</small>
            <ul v-if="turn.topics.length" class="assistant-topics" :inert="applying">
              <li v-for="topic in turn.topics" :key="topic.id">
                <HelpLink :page="topic.page" :anchor="topic.anchor" :label="topic.title" icon="help" />
                <SettingsAssistantLink :to="target(topic.section)" :after-navigation="afterNavigation">
                  打开{{ pages.get(topic.section) }}<AppIcon name="chevron-right" :size="13" />
                </SettingsAssistantLink>
              </li>
            </ul>
          </template>
        </SettingsAssistantReply>
        <SettingsAssistantSuggestion v-if="turn.suggestion && i === conversation.length - 1"
          :suggestion="turn.suggestion" :libraries="libraries" :libraries-ready="librariesReady"
          :libraries-error="librariesError" :refresh-libraries="refreshLibraries" :dirty-libraries="dirtyLibraries"
          :rules-busy="rulesBusy" :after-navigation="afterNavigation" @busy="setApplying"
          @rules-saved="(id, names) => emit('rules-saved', id, names)" @display-saved="emit('display-saved', $event)" />
      </div>
      <SettingsAssistantReply v-if="readiness !== 'ready'">
        <p v-if="readiness === 'loading'" role="status">正在读取智能辅助配置…</p>
        <template v-else>
          <p v-if="readiness === 'unavailable'" role="alert">暂时无法读取智能辅助配置。{{ configError }}</p>
          <p v-else role="status">{{ setupNotice }}</p>
          <div class="assistant-setup-actions">
            <JzButton v-if="readiness === 'unavailable'" ref="setupAction" @click="loadStatus()">重试</JzButton>
            <JzButton v-else ref="setupAction" variant="primary" @click="emit('configure')">去配置智能辅助</JzButton>
            <HelpLink page="user-guide/settings" anchor="智能辅助" label="查看配置教程" />
            <HelpLink page="user-guide/settings" label="打开设置手册" />
          </div>
        </template>
      </SettingsAssistantReply>
    </div>
    <template v-if="readiness === 'ready'" #footer>
      <form class="assistant-form" @submit.prevent="ask">
        <input id="settings-question" ref="input" v-model="question" type="text" autocomplete="off" maxlength="500"
          :aria-label="turns.length ? '继续追问' : '设置问题'" :placeholder="asking ? '等待助手回复…' : turns.length ? '继续追问…' : '输入你的问题…'"
          :disabled="applying || asking" aria-describedby="settings-question-privacy" @keydown.enter="guardComposition" />
        <JzButton type="submit" class="assistant-send" icon-only icon="arrow-up" variant="primary" aria-label="发送" title="发送"
          :disabled="!question.trim() || applying || asking" />
      </form>
    </template>
  </JzDialog>
</template>

<script setup>
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import { api } from '../api.js'
import AppIcon from './AppIcon.vue'
import SettingsAssistantReply from './SettingsAssistantReply.vue'
import { SETTINGS_PAGES, SETTINGS_TASK_PAGES } from '../settingsNavigation.js'
import HelpLink from './HelpLink.vue'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import SettingsAssistantLink from './SettingsAssistantLink.vue'
import SettingsAssistantSuggestion from './SettingsAssistantSuggestion.vue'

const props = defineProps({
  section: { type: String, required: true }, libraries: { type: Array, required: true },
  librariesReady: Boolean, librariesError: String, refreshLibraries: { type: Function, required: true },
  dirtyLibraries: { type: Array, required: true }, rulesBusy: Boolean,
  docked: Boolean, corner: { type: String, default: 'bottom-right' },
  contextKey: { type: String, default: '' }, settingsVersion: { type: Number, default: 0 },
})
const emit = defineEmits(['close', 'rules-saved', 'display-saved', 'configure', 'speaking', 'busy', 'update:corner'])
const pages = new Map([...SETTINGS_PAGES, ...SETTINGS_TASK_PAGES].map(page => [page.id, page.label]))
const target = sec => ({ path: '/settings', query: { sec } })
const question = ref(''), turns = ref([]), latest = ref(null), input = ref(null), panel = ref(null)
const examples = ['怎么调整匹配优先级？', '帮我把字体调大一点']
const setupAction = ref(null), privacy = ref(null), thread = ref(null)
const transient = ref(null)
const conversation = computed(() => transient.value ? [...turns.value, transient.value] : turns.value)
function prefill(text) {
  question.value = text
  input.value?.focus({ preventScroll: true })
}
let compositionEnterTimer
function guardComposition(event) {
  // Let the IME commit its candidate. Ignore an implicit form submission from this same key.
  if (event.isComposing || event.keyCode === 229) {
    clearTimeout(compositionEnterTimer)
    compositionEnterTimer = setTimeout(() => { compositionEnterTimer = undefined }, 0)
  }
}
function leavePrivacy(event) {
  if (privacy.value && !privacy.value.contains(event.relatedTarget)) privacy.value.open = false
}
function dismissPrivacy(event) {
  if (privacy.value?.open) {
    event.preventDefault()
    event.stopPropagation()
    privacy.value.open = false
    privacy.value.querySelector('summary')?.focus()
  }
}
const asking = ref(false), applying = ref(false)
const readiness = ref('loading'), configError = ref('')
const setupNotice = computed(() => ({
  disabled: '智能辅助尚未启用。请在智能辅助设置中启用服务并保存，即可对话。',
  missing: '当前模型服务还缺少 API Key。请在智能辅助设置中填写密钥并保存。',
  invalid: '已保存的模型配置无效，请核对智能辅助设置。',
}[readiness.value]))
let controller, statusController, speechTimer, turnId = 0, discardAfterApply = false

function stopSpeaking() {
  clearTimeout(speechTimer)
  emit('speaking', false)
}
function discardSuggestion() {
  if (turns.value.length) turns.value.at(-1).suggestion = null
}
function invalidate() {
  controller?.abort()
  asking.value = false
  if (transient.value && !question.value) question.value = transient.value.question
  transient.value = null
  stopSpeaking()
  if (applying.value) discardAfterApply = true
  else discardSuggestion()
}
watch([() => props.section, () => props.contextKey], invalidate, { flush: 'sync' })
watch(() => props.settingsVersion, () => { invalidate(); loadStatus() })
onMounted(() => loadStatus(true))
onScopeDispose(() => {
  clearTimeout(compositionEnterTimer)
  controller?.abort()
  statusController?.abort()
  stopSpeaking()
})
async function loadStatus(focusOnOpen = false) {
  statusController?.abort()
  const request = statusController = new AbortController()
  const previouslyFocused = document.activeElement
  readiness.value = 'loading'
  configError.value = ''
  try {
    const saved = await api('/api/ai/settings', { method: 'GET', signal: request.signal, cache: 'no-store' })
    if (request.signal.aborted) return
    if (typeof saved?.enabled !== 'boolean' || !['deepseek', 'opencode_go', 'compatible'].includes(saved.provider) ||
      (saved.provider !== 'compatible' && typeof saved.api_key_set !== 'boolean'))
      throw new Error('未获得有效的模型配置状态。')
    if (!saved.enabled) readiness.value = 'disabled'
    else if (saved.provider !== 'compatible' && !saved.api_key_set) readiness.value = 'missing'
    else readiness.value = 'ready'
  } catch (e) {
    if (request.signal.aborted) return
    readiness.value = e.status === 400 ? 'invalid' : 'unavailable'
    configError.value = e.message
  }
  await nextTick()
  if (!focusOnOpen || request.signal.aborted || !panel.value?.canFocus() ||
    !window.matchMedia('(pointer: fine) and (min-width: 701px)').matches) return
  if (document.activeElement === previouslyFocused || panel.value?.element?.contains(document.activeElement)) {
    const control = input.value || setupAction.value
    control?.focus({ preventScroll: true })
  }
}
function setApplying(value) {
  applying.value = value
  emit('busy', value)
  if (!value && discardAfterApply) { discardAfterApply = false; discardSuggestion() }
}
function afterNavigation() { if (!props.docked) emit('close') }
function keepLatest(el, index) {
  if (el && index === conversation.value.length - 1) latest.value = el
}

async function ask() {
  const text = question.value.trim()
  if (!text || readiness.value !== 'ready' || asking.value || applying.value || compositionEnterTimer !== undefined) return
  asking.value = true
  transient.value = { id: ++turnId, question: text }
  question.value = ''
  const request = controller = new AbortController()
  discardSuggestion()
  clearTimeout(speechTimer)
  emit('speaking', true)
  try {
    nextTick(() => {
      const body = thread.value?.parentElement
      if (!request.signal.aborted && body) body.scrollTop = body.scrollHeight
    })
    const history = turns.value.slice(-4).map(({ question, answer }) => ({ question, answer }))
    const result = await api('/api/ai/settings-assistant', {
      method: 'POST', signal: request.signal,
      body: JSON.stringify({ question: text, section: props.section, history }),
    })
    if (request.signal.aborted) return
    turns.value.push({ id: transient.value.id, question: text, answer: result.answer, mode: result.mode,
      topics: result.topics.filter(topic => pages.has(topic.section)),
      suggestion: result.mode === 'ai' ? result.suggestion : null })
    transient.value = null
    await nextTick()
    if (!request.signal.aborted && panel.value?.canFocus() && (document.activeElement === document.body ||
      panel.value?.element?.contains(document.activeElement))) {
      latest.value?.focus({ preventScroll: true })
      const body = thread.value?.parentElement
      if (body && latest.value) {
        const offset = latest.value.getBoundingClientRect().top - body.getBoundingClientRect().top
        if (offset >= 0) body.scrollTop += offset - 10
      }
    }
    // A local answer may follow a service change in another tab or device.
    // Recheck the saved status without probing the provider or losing the reply.
    if (!request.signal.aborted && result.mode === 'local') loadStatus()
  } catch (e) {
    if (!request.signal.aborted) {
      transient.value = { ...transient.value, error: '提问失败，可重新发送。' + e.message }
      question.value = text
    }
  } finally {
    if (!request.signal.aborted) {
      asking.value = false
      speechTimer = setTimeout(stopSpeaking, 1200)
    }
  }
}
</script>

<style>
/* This glass belongs to the assistant; other shared dialogs retain their solid surfaces. */
#settings-assistant-panel {
  --chat-incoming: rgb(62 62 69 / 94%);
  --chat-outgoing: #963544;
  --chat-line: rgb(255 255 255 / 12%);
  --chat-link: #bfdcff;
  width: 100%; height: auto; min-height: 0; max-height: var(--assistant-max-height, 480px);
  border: 1px solid rgb(255 255 255 / 20%); border-radius: 19px;
  background: rgb(35 35 40 / 97%); color: var(--jz-text);
  box-shadow: 0 16px 48px rgb(0 0 0 / 36%), 0 3px 12px rgb(0 0 0 / 22%);
}
@supports ((backdrop-filter: blur(10px)) or (-webkit-backdrop-filter: blur(10px))) {
  #settings-assistant-panel { background: rgb(41 40 46 / 60%); -webkit-backdrop-filter: blur(7px) saturate(115%); backdrop-filter: blur(7px) saturate(115%); }
}
#settings-assistant-panel .jz-dialog-header { position: relative; align-items: center; gap: 0; padding: 8px 12px 8px 17px; border-color: var(--chat-line); }
#settings-assistant-panel .jz-dialog-heading { min-width: 0; }
#settings-assistant-panel .jz-dialog-heading h2 { margin: 0; color: var(--jz-text); font-size: 1rem; font-weight: 650; line-height: 1.3; }
#settings-assistant-panel .jz-dialog-close { width: 34px; min-width: 34px; min-height: 34px; margin: 0; padding: 0; border-radius: 50%; }
#settings-assistant-panel .jz-dialog-body { flex: 1 1 auto; padding: 16px 14px; font-size: var(--jz-font-m); scrollbar-width: thin; }
#settings-assistant-panel .jz-dialog-footer { display: block; padding: 0 12px 12px; border-top: 0; }
@media (prefers-reduced-transparency: reduce) {
  #settings-assistant-panel { background: var(--jz-surface); -webkit-backdrop-filter: none; backdrop-filter: none; }
}
@media (max-width: 700px), (pointer: coarse) {
  #settings-assistant-panel .jz-dialog-header { padding: 6px 6px 6px 14px; }
  #settings-assistant-panel .jz-dialog-close { width: 44px; min-width: 44px; min-height: 44px; }
}
</style>

<style scoped>
.assistant-heading { display: flex; align-items: center; min-width: 0; }
.assistant-title { flex: 1; min-width: 0; }
.assistant-title span { display: block; margin-top: 2px; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assistant-privacy summary, .assistant-dock { display: grid; place-items: center; position: relative; width: 34px; height: 34px; border-radius: 50%; color: var(--jz-text-dim); cursor: pointer; }
.assistant-privacy summary::-webkit-details-marker { display: none; }
.assistant-privacy summary:hover, .assistant-privacy[open] summary, .assistant-dock:hover { color: var(--jz-text); background: var(--chat-line); }
.assistant-dock:focus-within, .assistant-privacy summary:focus-visible { outline: 2px solid var(--jz-link); outline-offset: -2px; }
.assistant-dock select { position: absolute; inset: 0; width: 100%; min-height: 100%; opacity: 0; padding: 0; cursor: pointer; }
.assistant-privacy p { position: absolute; z-index: 2; top: calc(100% - 3px); right: 12px; left: 12px; margin: 0; padding: 12px 14px; border: 1px solid var(--jz-border-strong); border-radius: 10px; background: var(--jz-surface-2); box-shadow: var(--jz-shadow-menu); font-size: var(--jz-font-m); line-height: 1.6; }
.assistant-thread { display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: 16px; }
.assistant-turn { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: 12px; }
.assistant-question { justify-self: end; max-width: 85%; box-sizing: border-box; margin: 0; padding: 9px 13px; border-radius: 15px 5px 15px 15px; background: var(--chat-outgoing); color: var(--jz-on-accent); font: inherit; line-height: 1.55; overflow-wrap: anywhere; white-space: pre-line; text-wrap: wrap; }
.assistant-question:focus { outline: none; }
.assistant-answer { line-height: 1.62; white-space: pre-line; }
.assistant-welcome { display: grid; gap: 9px; }
.assistant-starters { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 6px; margin-left: 35px; }
.assistant-starters .jz-button { min-height: 30px; max-width: 100%; padding: 5px 10px; border-color: var(--chat-line); border-radius: 14px; background: rgb(28 28 33 / 55%); color: var(--jz-text); font-weight: 400; text-align: left; }
.assistant-starters .jz-button:hover { border-color: var(--jz-text-faint); }
.assistant-starters svg { color: var(--jz-text-dim); }
.assistant-source { display: block; margin-top: 6px; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.assistant-topics { display: grid; gap: 7px; margin: 9px 0 0; padding: 9px 0 0; border-top: 1px solid var(--chat-line); list-style: none; }
.assistant-topics li { display: flex; align-items: center; flex-wrap: wrap; gap: 2px 12px; }
.assistant-topics a { display: inline-flex; align-items: center; gap: 4px; color: var(--chat-link); font-size: var(--jz-font-s); text-decoration: none; }
.assistant-topics a:hover { text-decoration: underline; }
.assistant-topics :deep(.help-link > svg) { width: 15px; height: 15px; }
.assistant-setup-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-top: 10px; }
.assistant-setup-actions .jz-button { border-radius: 10px; }
.assistant-failure p { margin: 0 0 5px; }
.assistant-typing { display: flex; align-items: center; gap: 4px; min-height: 20px; }
.assistant-typing > span[aria-hidden] { width: 5px; height: 5px; border-radius: 50%; background: var(--jz-text-dim); }
@media (prefers-reduced-motion: no-preference) {
  .assistant-typing > span[aria-hidden] { animation: assistant-typing 1.2s ease-in-out infinite; }
  .assistant-typing > span:nth-child(2) { animation-delay: .14s; }
  .assistant-typing > span:nth-child(3) { animation-delay: .28s; }
  @keyframes assistant-typing { 0%, 65%, 100% { opacity: .5; } 30% { opacity: 1; transform: translateY(-2px); } }
}
.assistant-sr { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.assistant-form { display: flex; align-items: center; width: 100%; min-width: 0; box-sizing: border-box; padding: 4px 5px 4px 12px; gap: 4px; background: rgb(18 18 21 / 77%); border: 1px solid var(--chat-line); border-radius: 22px; }
.assistant-form:focus-within { border-color: var(--jz-text-faint); }
.assistant-form input { flex: 1; width: 0; min-width: 0; min-height: 30px; box-sizing: border-box; padding: 5px 0; border: 0; border-radius: 0; background: transparent; color: var(--jz-text); font: inherit; font-size: var(--jz-font-m); }
.assistant-form input:focus-visible { outline: none; }
.assistant-send.jz-button { flex: none; width: 32px; min-width: 32px; min-height: 32px; height: 32px; padding: 0; border-radius: 50%; }
.assistant-send.jz-button:disabled { opacity: 1; border-color: transparent; background: var(--chat-incoming); color: var(--jz-text-dim); }
@media (max-width: 700px), (pointer: coarse) {
  .assistant-privacy summary, .assistant-dock, .assistant-send.jz-button { width: 44px; min-width: 44px; height: 44px; min-height: 44px; }
  .assistant-form input { min-height: 40px; font-size: 1rem; }
  .assistant-form { border-radius: 28px; }
  .assistant-topics li { display: grid; grid-template-columns: minmax(0, 1fr) fit-content(45%); gap: 7px; }
  .assistant-topics a { min-width: 0; line-height: 1.45; }
  .assistant-topics a, .assistant-starters .jz-button, .assistant-setup-actions a { min-height: 44px; }
}
</style>
