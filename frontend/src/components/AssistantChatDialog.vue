<template>
  <AssistantPanel :id="panelId" ref="panel" :subtitle="browse ? '浏览助手' : '设置助手'" :section="browse ? channelLabel : pages.get(section)"
    :close-label="browse ? '收起小映' : '关闭设置助手'" :label-by-subtitle="!browse" :docked="docked" :busy="applying" :corner="corner"
    info-label="对话隐私" :info="privacyText"
    @close="emit('close')" @update:corner="emit('update:corner', $event)">
    <div ref="thread" class="assistant-thread" aria-label="聊天记录">
      <div v-if="browse && ($slots.welcome || $slots.preferences)" class="assistant-context-tools">
        <JzButton v-if="conversation.length" size="compact" variant="ghost" :aria-expanded="showPageTips"
          aria-controls="assistant-page-tips" @click="showPageTips = !showPageTips">本页提示<AppIcon :name="showPageTips ? 'chevron-up' : 'chevron-down'" :size="14" /></JzButton>
        <div v-if="!conversation.length || showPageTips" id="assistant-page-tips" class="assistant-page-resources">
          <slot name="welcome" /><slot name="preferences" />
        </div>
      </div>
      <div v-if="!browse && !turns.length && !transient && (readiness === 'ready' || section === 'sec-online')" class="assistant-welcome">
        <SettingsAssistantReply>
          <p v-if="section === 'sec-online'" class="assistant-answer">哪个来源播不了？告诉我来源名称，我可以检查接口和播放线路。你也可以直接让我准备换址方案，核对后再保存。</p>
          <p v-else class="assistant-answer">你好，我是小映，片场的小放映机。设置遇到难题可以问我，也可以喊我跳支舞。</p>
        </SettingsAssistantReply>
        <div class="assistant-starters" role="group" aria-label="填入示例问题">
          <JzButton v-for="example in examples" :key="example" size="compact" @click="prefill(example)">
            {{ example }}<AppIcon name="arrow-right" :size="14" />
          </JzButton>
        </div>
      </div>
      <SettingsAssistantReply v-if="!browse && readiness !== 'ready'">
        <p v-if="readiness === 'loading'" role="status">正在读取智能辅助配置…</p>
        <template v-else>
          <p v-if="readiness === 'unavailable'" role="alert">暂时无法读取智能辅助配置。{{ configError }}</p>
          <p v-else role="status">{{ setupNotice }}</p>
          <div class="assistant-setup-actions">
            <JzButton v-if="readiness === 'unavailable'" @click="loadStatus">重试</JzButton>
            <JzButton v-else variant="primary" @click="emit('configure')">去配置智能辅助</JzButton>
            <HelpLink page="user-guide/settings" anchor="智能辅助" label="查看配置教程" />
            <HelpLink page="user-guide/settings" label="打开设置手册" />
          </div>
        </template>
      </SettingsAssistantReply>
      <div v-for="(turn, i) in conversation" :key="turn.id" class="assistant-turn"
        :class="{ 'assistant-pending': turn === transient && asking }">
        <component :is="turn === transient ? 'p' : 'h3'" :ref="el => keepLatest(el, i)"
          class="assistant-question" tabindex="-1">{{ turn.question }}</component>
        <SettingsAssistantReply :arriving="turn.id === arrivingId" :reaction="turn.id === arrivingId ? turn.reaction : null">
          <div v-if="turn === transient && asking" class="assistant-typing" role="status">
            <span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>
            <span class="assistant-sr">小映正在回复…</span>
          </div>
          <div v-else-if="turn.error" role="alert" class="assistant-failure">
            <p>{{ turn.error }}</p><HelpLink :page="browse ? guide : 'user-guide/settings'" :label="browse ? '查看本页帮助' : '打开设置手册'" />
          </div>
          <template v-else>
            <p class="assistant-answer">{{ turn.diagnosisAnswer && activeDiagnosis !== turn.id ? turn.diagnosisAnswer : turn.answer }}</p>
            <div v-if="browse && turn.reviewSource && viewingMode === 'normal' && i === conversation.length - 1" class="assistant-starters">
              <JzButton size="compact" @click="emit('review-source', turn.question)">在设置中预览换址</JzButton>
            </div>
            <small v-if="turn.mode === 'local'" class="assistant-source">{{ browse || turn.localSourceChange || turn.diagnosis ? '本地回答' : '回答来自本版手册' }}</small>
            <ul v-if="!browse && turn.topics.length" class="assistant-topics" :inert="applying">
              <li v-for="topic in turn.topics" :key="topic.id">
                <HelpLink :page="topic.page" :anchor="topic.anchor" :label="topic.title" icon="help" />
                <SettingsAssistantLink :to="target(topic.section)" :after-navigation="afterNavigation">
                  打开{{ pages.get(topic.section) }}<AppIcon name="chevron-right" :size="13" />
                </SettingsAssistantLink>
              </li>
            </ul>
          </template>
        </SettingsAssistantReply>
        <AssistantOnlineDiagnosis v-if="activeDiagnosis === turn.id && !turn.error"
          :ref="el => { diagnosisCard = el }" :question="turn.question" :template-id="turn.diagnosisTemplate"
          :context="turnContext" :needs-unlock="needsDiagnosisUnlock" :access="onlineSources"
          @busy="setApplying" @summary="turn.diagnosisAnswer = $event" @unlock="emit('unlock-diagnosis', $event)" @replay="emit('replay', $event)" />
        <AssistantSourceMigration v-else-if="!browse && turn.suggestion?.kind === 'online_source' && i === conversation.length - 1"
          :suggestion="turn.suggestion" :access="onlineSources" @busy="setApplying" />
        <SettingsAssistantSuggestion v-else-if="!browse && turn.suggestion && i === conversation.length - 1"
          :suggestion="turn.suggestion" :libraries="libraries" :libraries-ready="librariesReady"
          :libraries-error="librariesError" :refresh-libraries="refreshLibraries" :dirty-libraries="dirtyLibraries"
          :rules-busy="rulesBusy" :after-navigation="afterNavigation" @busy="setApplying"
          @rules-saved="(id, names) => emit('rules-saved', id, names)" @display-saved="emit('display-saved', $event)" />
      </div>
    </div>
    <template #footer>
      <form class="assistant-form assistant-footer-control" @submit.prevent="ask">
        <input :id="browse ? 'assistant-question' : 'settings-question'" ref="input" v-model="question" type="text" autocomplete="off" maxlength="500"
          :aria-label="turns.length ? '继续追问' : browse ? '向小映提问' : '设置问题'" :placeholder="asking ? '等待助手回复…' : turns.length ? '继续追问…' : '输入你的问题…'"
          :disabled="applying || asking" :aria-describedby="panelId + '-info'" @keydown.enter="guardComposition" />
        <JzButton type="submit" class="assistant-send" icon-only icon="arrow-up" variant="primary" aria-label="发送" title="发送"
          :disabled="!question.trim() || applying || asking" />
      </form>
    </template>
  </AssistantPanel>
</template>

<script setup>
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import { api } from '../api.js'
import AppIcon from './AppIcon.vue'
import SettingsAssistantReply from './SettingsAssistantReply.vue'
import { SETTINGS_PAGES, SETTINGS_TASK_PAGES } from '../settingsNavigation.js'
import HelpLink from './HelpLink.vue'
import JzButton from './JzButton.vue'
import AssistantPanel from './AssistantPanel.vue'
import SettingsAssistantLink from './SettingsAssistantLink.vue'
import SettingsAssistantSuggestion from './SettingsAssistantSuggestion.vue'
import AssistantOnlineDiagnosis from './AssistantOnlineDiagnosis.vue'
import AssistantSourceMigration from './AssistantSourceMigration.vue'

const props = defineProps({
  channel: String, channelLabel: String, guide: String, viewingMode: { type: String, default: 'normal' },
  initialSourceRequest: String, initialDiagnosisQuestion: String, initialDiagnosisContext: Object, diagnosisContext: Object, needsDiagnosisUnlock: Boolean,
  section: String, libraries: Array, librariesReady: Boolean, librariesError: String, refreshLibraries: Function,
  dirtyLibraries: Array, rulesBusy: Boolean, onlineSources: Object,
  docked: Boolean, corner: { type: String, default: 'bottom-right' },
  contextKey: { type: String, default: '' }, settingsVersion: { type: Number, default: 0 },
})
const emit = defineEmits(['close', 'rules-saved', 'display-saved', 'configure', 'motion', 'busy', 'update:corner', 'review-source', 'unlock-diagnosis', 'replay'])
// The browser host owns completed browsing turns across routes. Settings keeps
// its own thread in this dialog, with its grants and explicit write confirmations.
const turns = defineModel('history', { type: Array, default: () => [] })
const browse = computed(() => !!props.channel)
const panelId = computed(() => browse.value ? 'assistant-page-panel' : 'settings-assistant-panel')
const privacyText = computed(() => {
  if (!browse.value) return '常见问候和表演由小映在本地回应。需要模型回答时，会发送问题、近期对话、当前分区和相关手册摘录。密钥、令牌和媒体路径请在原设置表单填写。'
  if (props.viewingMode !== 'normal') return '当前观看模式使用本地回答。小映可以回应常见问候、做动作，也可以介绍本页用法。'
  return '问候和常见表演由小映在本地回应。需要模型回答时，会发送你的问题、近期对话、当前频道和相关手册内容。'
})
const pages = new Map([...SETTINGS_PAGES, ...SETTINGS_TASK_PAGES].map(page => [page.id, page.label]))
const target = sec => ({ path: '/settings', query: { sec } })
const question = ref(''), latest = ref(null), input = ref(null), panel = ref(null)
const examples = computed(() => props.section === 'sec-online' ? ['为什么在线资源无法播放了？', '帮我更新量子资源接口', '去哪里找视频源？']
  : ['怎么调整匹配优先级？', '帮我把字体调大一点'])
const reactions = new Set(['wave', 'smile', 'cry', 'dance'])
const thread = ref(null), showPageTips = ref(false)
const activeDiagnosis = ref(null), diagnosisCard = ref(null), turnContext = ref(null)
let resumingDiagnosis = false
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
const asking = ref(false), applying = ref(false)
const arrivingId = ref(null)
watch(() => arrivingId.value !== null ? (turns.value.at(-1)?.reaction || 'reply') : asking.value ? 'thinking' : 'idle',
  motion => emit('motion', motion))
const readiness = ref('loading'), configError = ref('')
const setupNotice = computed(() => ({
  disabled: '智能辅助尚未启用。你可以直接打招呼、请我做动作，或问本版手册里的设置。',
  missing: '当前模型服务还缺少 API Key。小映可以先用本地手册回答设置问题。',
  invalid: '已保存的模型配置无效。小映可以先用本地手册回答设置问题。',
}[readiness.value]))
let controller, statusController, arrivalTimer, discardAfterApply = false

function endArrival() {
  clearTimeout(arrivalTimer)
  arrivingId.value = null
}
function discardSuggestion() {
  if (!browse.value && turns.value.length) turns.value.at(-1).suggestion = null
}
function invalidate() {
  activeDiagnosis.value = null; resumingDiagnosis = false
  controller?.abort()
  asking.value = false
  if (transient.value && !question.value) question.value = transient.value.question
  transient.value = null
  endArrival()
  if (applying.value) discardAfterApply = true
  else discardSuggestion()
}
watch([() => props.section, () => props.contextKey, () => props.channel], () => {
  invalidate()
  showPageTips.value = false
}, { flush: 'sync' })
watch(() => [props.diagnosisContext?.source_id, props.diagnosisContext?.item_id, props.diagnosisContext?.external_id,
  props.diagnosisContext?.episode_id, props.diagnosisContext?.line_id, props.diagnosisContext?.cache_only].join('|'),
  () => { if (!resumingDiagnosis && (asking.value || activeDiagnosis.value != null)) invalidate() }, { flush: 'sync' })
watch(() => props.needsDiagnosisUnlock, invalidate, { flush: 'sync' })
watch(() => props.settingsVersion, () => { invalidate(); loadStatus() })
onMounted(async () => {
  const previouslyFocused = document.activeElement
  if (!browse.value) void loadStatus()
  if (props.initialDiagnosisQuestion && props.viewingMode === 'normal') {
    question.value = props.initialDiagnosisQuestion
    void ask(true)
  } else if (!browse.value && props.section === 'sec-online' && props.initialSourceRequest) {
    question.value = props.initialSourceRequest
    void ask()
  }
  await nextTick()
  if (!panel.value?.canFocus() || !window.matchMedia('(pointer: fine) and (min-width: 701px)').matches) return
  if (document.activeElement === previouslyFocused || panel.value.element?.contains(document.activeElement)) {
    input.value?.focus({ preventScroll: true })
  }
})
onScopeDispose(() => {
  clearTimeout(compositionEnterTimer)
  controller?.abort()
  statusController?.abort()
  clearTimeout(arrivalTimer)
  emit('motion', 'idle')
})
async function loadStatus() {
  if (browse.value) return
  statusController?.abort()
  const request = statusController = new AbortController()
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

async function ask(resume = false) {
  const text = question.value.trim()
  if (!text || asking.value || applying.value || compositionEnterTimer !== undefined) return
  const reply = text.replace(/[。！!？?\s]+$/u, '')
  const agrees = /^(同意|我同意|确认|好的|可以|同意修改|确认修改|确认更新|可以修改)$/.test(reply)
  if (turns.value.at(-1)?.diagnosis && (agrees || /^(取消|不同意|先不改|不改了|不用了|算了)$/.test(reply))) {
    question.value = ''
    if (diagnosisCard.value) diagnosisCard.value.handleReply(agrees)
    else transient.value = { id: turns.value.at(-1).id + 1, question: text, answer: '目前没有等待确认的方案，请重新描述播放问题。', topics: [] }
    return
  }
  activeDiagnosis.value = null
  const live = props.diagnosisContext, saved = props.initialDiagnosisContext
  resumingDiagnosis = resume === true && !!saved && (saved.source_id !== live?.source_id || saved.item_id !== live?.item_id ||
    saved.episode_id !== live?.episode_id || saved.line_id !== live?.line_id)
  turnContext.value = resumingDiagnosis ? saved : live
  asking.value = true
  showPageTips.value = false
  transient.value = { id: (turns.value.at(-1)?.id ?? 0) + 1, question: text }
  question.value = ''
  const request = controller = new AbortController()
  discardSuggestion()
  endArrival()
  try {
    nextTick(() => {
      const body = thread.value?.parentElement
      if (!request.signal.aborted && body) body.scrollTop = body.scrollHeight
    })
    const history = turns.value.slice(-4).map(({ question, answer }) => ({ question, answer }))
    const context = browse.value ? { channel: props.channel } : { section: props.section }
    const result = await api(browse.value ? '/api/ai/assistant' : '/api/ai/settings-assistant', {
      method: 'POST', signal: request.signal,
      ...(browse.value ? { headers: { 'X-Viewing-Mode': props.viewingMode } } : {}),
      body: JSON.stringify({ question: text, history, ...context }),
    })
    if (request.signal.aborted) return
    if (typeof result?.answer !== 'string' || !result.answer.trim() || !['ai', 'social', 'local'].includes(result.mode)
      || (!browse.value && !Array.isArray(result.topics))) throw new Error('未收到有效回复。')
    const social = result.mode === 'social'
    const localSourceChange = !browse.value && result.mode === 'local' && result.suggestion?.kind === 'online_source'
    const diagnosis = result.mode === 'local' && result.diagnose_online === true && props.viewingMode === 'normal'
    if (diagnosis) activeDiagnosis.value = transient.value.id
    const modelSuggestion = !browse.value && result.mode === 'ai' && ['display', 'matching'].includes(result.suggestion?.kind)
    const reaction = social && reactions.has(result.reaction) ? result.reaction : null
    turns.value = [...turns.value, { id: transient.value.id, question: text, answer: result.answer, mode: result.mode, reaction,
      diagnosis, diagnosisTemplate: diagnosis ? result.diagnosis_template_id : undefined,
      reviewSource: !diagnosis && browse.value && props.viewingMode === 'normal' && result.mode === 'local' && result.open_settings === 'online_source',
      topics: social || browse.value ? [] : result.topics.filter(topic => pages.has(topic.section)),
      localSourceChange, suggestion: !diagnosis && (localSourceChange || modelSuggestion) ? result.suggestion : null }]
    arrivingId.value = transient.value.id
    arrivalTimer = setTimeout(endArrival, reaction ? 2000 : 1200)
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
    if (!request.signal.aborted && !browse.value && result.mode === 'local') loadStatus()
  } catch (e) {
    if (!request.signal.aborted) {
      transient.value = { ...transient.value, error: '提问失败，可重新发送。' + e.message }
      question.value = text
    }
  } finally {
    if (!request.signal.aborted) asking.value = false
  }
}
</script>

<style scoped>
.assistant-context-tools { min-width: 0; }
.assistant-page-resources { display: grid; gap: 9px; min-width: 0; }
.assistant-context-tools > button + .assistant-page-resources { margin-top: 10px; }
.assistant-turn { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: 12px; }
.assistant-question { justify-self: end; max-width: 85%; box-sizing: border-box; margin: 0; padding: 9px 13px; border-radius: 15px 5px 15px 15px; background: var(--chat-outgoing); color: var(--jz-on-accent); font: inherit; line-height: 1.55; overflow-wrap: anywhere; white-space: pre-line; text-wrap: wrap; }
.assistant-question:focus { outline: none; }
.assistant-source { display: block; margin-top: 6px; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.assistant-setup-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-top: 10px; }
.assistant-setup-actions .jz-button { border-radius: 10px; }
.assistant-failure p { margin: 0 0 5px; }
.assistant-typing { display: flex; align-items: center; gap: 4px; min-height: 20px; }
.assistant-typing > span[aria-hidden] { width: 5px; height: 5px; border-radius: 50%; background: var(--jz-text-dim); }
@media (prefers-reduced-motion: no-preference) {
  .assistant-pending .assistant-question { animation: assistant-question-in 150ms ease-out; }
  @keyframes assistant-question-in { from { opacity: .5; transform: translateY(3px); } }
  .assistant-typing > span[aria-hidden] { animation: assistant-typing 1.2s ease-in-out infinite; }
  .assistant-typing > span:nth-child(2) { animation-delay: .14s; }
  .assistant-typing > span:nth-child(3) { animation-delay: .28s; }
  @keyframes assistant-typing { 0%, 65%, 100% { opacity: .5; } 30% { opacity: 1; transform: translateY(-2px); } }
}
.assistant-sr { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.assistant-form { padding: 4px 5px 4px 12px; gap: 4px; }
.assistant-form input { flex: 1; width: 0; min-width: 0; min-height: 30px; box-sizing: border-box; padding: 5px 0; border: 0; border-radius: 0; background: transparent; color: var(--jz-text); font: inherit; font-size: var(--jz-font-m); }
.assistant-form input:focus-visible { outline: none; }
.assistant-send.jz-button { flex: none; width: 32px; min-width: 32px; min-height: 32px; height: 32px; padding: 0; border-radius: 50%; }
.assistant-send.jz-button:disabled { opacity: 1; border-color: transparent; background: var(--chat-incoming); color: var(--jz-text-dim); }
@media (max-width: 700px), (pointer: coarse) {
  .assistant-send.jz-button { width: 44px; min-width: 44px; height: 44px; min-height: 44px; }
  .assistant-form input { min-height: 40px; font-size: 1rem; }
  .assistant-setup-actions a { min-height: 44px; }
}
</style>
