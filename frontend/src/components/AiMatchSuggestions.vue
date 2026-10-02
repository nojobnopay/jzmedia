<template>
  <section class="ai-match" aria-label="AI 匹配建议">
    <div class="bar"><button :disabled="busy || disabled" @click="suggest">{{ busy ? '正在分析候选…' : 'AI 匹配建议' }}</button><button v-if="busy" @click="reset">取消等待</button></div>
    <p class="hint">点击后发送片名、文件名等必要线索，结合真实资料候选给出建议。请核对年份、标题与来源后确认绑定。</p>
    <p v-if="error" role="status">{{ error }} 可继续用上方搜索手动匹配。<router-link :to="{ path: '/settings', query: { sec: 'sec-ai' } }">配置智能辅助</router-link></p>
    <template v-if="result">
      <p>{{ result.summary }}</p>
      <p v-if="result.query" class="hint">建议查询：{{ result.query }} {{ result.year || '' }}</p>
      <ul v-if="result.warnings?.length"><li v-for="(warning, i) in result.warnings" :key="i">{{ warning }}</li></ul>
      <p v-if="!result.candidates?.length">没有可绑定的候选。请检查片名或年份，并用手动搜索补充线索。</p>
      <ul class="ai-candidates">
        <li v-for="(c, i) in result.candidates" :key="i">
          <strong>{{ c.title }} {{ String(c.year || c.release_date || c.first_air_date || '').slice(0, 4) }}</strong>
          <span class="hint"> · {{ c.source || 'TMDB' }}<template v-if="c.tmdb_id"> #{{ c.tmdb_id }}</template></span>
          <p v-if="c.original_title && c.original_title !== c.title" class="hint">{{ c.original_title }}</p>
          <p>{{ c.reason }}</p>
          <button v-if="bindable(c)" :disabled="disabled" @click="selected = c">选择此候选</button>
          <p v-else class="hint">{{ c.bind_reason || '仅索引线索，请手动搜索核对。' }}</p>
          <template v-if="selected === c">
            <p>确认将当前{{ kind === 'tv' ? '剧集' : '影片' }}绑定到「{{ c.title }}」？<span v-if="alreadyMatched">这会替换当前资料匹配。</span></p>
            <button :disabled="disabled" @click="confirm">确认绑定此候选</button><button :disabled="disabled" @click="selected = null">取消</button>
          </template>
        </li>
      </ul>
    </template>
  </section>
</template>
<script setup>
import { ref, watch } from 'vue'
import { useAiRequest } from '../useAiRequest.js'
import { canBindAiCandidate as bindable } from '../aiMatch.js'
const props = defineProps({ kind: { type: String, required: true }, itemId: { type: Number, required: true }, disabled: Boolean, alreadyMatched: Boolean })
const emit = defineEmits(['select'])
const { busy, result, error, run, reset } = useAiRequest()
const selected = ref(null)
watch(() => [props.kind, props.itemId], () => { reset(); selected.value = null }, { flush: 'sync' })
async function suggest() {
  if (props.disabled) return
  selected.value = null
  await run('/api/ai/match', { kind: props.kind, id: props.itemId })
}
function confirm() {
  const candidate = selected.value
  if (props.disabled || !candidate || !result.value?.candidates.includes(candidate) || !bindable(candidate)) return
  selected.value = null
  emit('select', candidate)
}
</script>
<style scoped>
.ai-match { margin: 16px 0; border-top: 1px solid var(--jz-border, #444); padding-top: 10px; }
.bar { display: flex; flex-wrap: wrap; gap: 8px; }
.hint { color: var(--jz-text-dim); font-size: .875rem; }
.ai-candidates { padding: 0; list-style: none; }
.ai-candidates li { border: 1px solid var(--jz-border, #444); border-radius: 8px; margin: 10px 0; padding: 12px; }
p { overflow-wrap: anywhere; }
a { color: var(--jz-link); }
</style>
