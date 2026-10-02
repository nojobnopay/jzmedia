<template>
  <section class="ai-panel" aria-label="智能搜索">
    <div class="ai-heading"><h3>智能搜索</h3><button @click="$emit('close')">收起</button></div>
    <p class="hint">描述想看的内容，解析后核对筛选条件。仅点击“解析条件”时调用模型；结果仍来自当前媒体库。</p>
    <label>想看什么<textarea v-model="term" rows="2" maxlength="500" placeholder="例如：没看过的、90 年代香港喜剧，TMDB 7 分以上" /></label>
    <div class="bar"><button :disabled="busy || !term.trim()" @click="parse">{{ busy ? '正在解析…' : '解析条件' }}</button><button v-if="busy" @click="reset">取消等待</button><router-link :to="{ path: '/settings', query: { sec: 'sec-ai' } }">配置智能辅助</router-link></div>
    <p v-if="error" role="status">{{ error }} 普通搜索仍可使用。</p>
    <div v-if="result && draft">
      <p>{{ result.summary }}</p>
      <ul v-if="result.warnings?.length"><li v-for="(warning, i) in result.warnings" :key="i">{{ warning }}</li></ul>
      <p class="hint">可直接修改以下条件，多项用逗号分隔；应用后会替换当前筛选，保留当前媒体库。</p>
      <div class="ai-fields">
        <label>搜索关键词<input v-model="draft.q" maxlength="200" /></label>
        <label v-for="field in AI_FILTER_FIELDS" :key="field.key">{{ field.label }}<input v-model="draft[field.key]" maxlength="300" /></label>
        <label>最低评分<input v-model="draft.min_rating" type="number" min="0" max="10" step="0.1" /></label>
        <label>评分来源<select v-model="draft.rating_source"><option value="tmdb">TMDB</option><option v-if="kind === 'movie'" value="douban">豆瓣</option><option value="custom">自评</option></select></label>
        <label>观看状态<select v-model="draft.watched"><option value="">不限</option><option :value="0">{{ kind === 'tv' ? '未看完' : '未看' }}</option><option :value="1">{{ kind === 'tv' ? '已看完' : '已看' }}</option></select></label>
        <label>排序<select v-model="draft.sort"><option v-for="s in WALL_SORTS" :key="s.key" :value="s.key">{{ s.label }}</option></select></label>
        <label>顺序<select v-model="draft.order"><option value="desc">降序</option><option value="asc">升序</option></select></label>
      </div>
      <fieldset v-if="kind === 'tv'"><legend>连载状态（不选即不限）</legend><label v-for="s in STATUS_OPTIONS" :key="s.value" class="check"><input type="checkbox" v-model="draft.status" :value="s.value" />{{ s.label }}</label></fieldset>
      <p v-if="draft.country.trim() && draft.region.trim()" class="hint">已指定国家／地区，应用时以国家／地区为准并清除产地大区条件；如需按大区筛选，请先清空国家／地区。</p>
      <p v-if="draft.q.trim() && kind === 'movie'" class="hint">含关键词时，电影结果按搜索相关度排序。</p>
      <p v-if="validationError" role="status">{{ validationError }}</p>
      <button class="primary" :disabled="!!validationError" @click="apply">确认应用条件</button>
    </div>
  </section>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { useAiRequest } from '../useAiRequest.js'
import { AI_FILTER_FIELDS, aiFilterDraft, aiFilterError, aiFiltersToWall } from '../aiSearch.js'
import { WALL_SORTS } from '../wallSort.js'
import { STATUS_OPTIONS } from '../tvWall.js'
const props = defineProps({ kind: { type: String, required: true }, query: { type: String, default: '' }, mediaLibraryId: { type: Number, default: null } })
const emit = defineEmits(['apply', 'close'])
const term = ref(props.query)
const draft = ref(null)
const { busy, result, error, run, reset } = useAiRequest()
const validationError = computed(() => aiFilterError(draft.value))
watch(term, reset, { flush: 'sync' })
watch(() => [props.kind, props.mediaLibraryId, props.query], () => { reset(); draft.value = null; term.value = props.query }, { flush: 'sync' })
async function parse() {
  if (!term.value.trim()) return
  const value = await run('/api/ai/search', { q: term.value.trim(), kind: props.kind, media_library_id: props.mediaLibraryId })
  if (value) draft.value = aiFilterDraft(value.filters)
}
function apply() {
  if (!result.value || !draft.value || validationError.value) return
  emit('apply', aiFiltersToWall(draft.value, props.kind))
}
</script>
<style scoped>
.ai-panel { border: 1px solid var(--jz-border, #444); border-radius: 10px; padding: 16px; margin: 12px 0; }
.ai-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h3 { margin: 0; }
label { display: flex; flex-direction: column; gap: 6px; }
textarea, input, select { min-width: 0; width: 100%; box-sizing: border-box; }
.ai-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 12px 0; }
.hint { color: var(--jz-text-dim); font-size: .875rem; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 12px 0; }
a { color: var(--jz-link); }
fieldset { margin: 12px 0; border: 1px solid var(--jz-border, #444); }
.check { display: inline-flex; flex-direction: row; align-items: center; margin-right: 12px; }
.check input { width: auto; }
</style>
