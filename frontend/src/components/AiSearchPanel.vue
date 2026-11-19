<template>
  <section class="discover" aria-label="智能搜索" :aria-busy="state.busy">
    <div v-if="!answer && !state.refreshing" class="discover-setup">
      <div class="discover-scope">
        <label>搜索范围<select v-model="state.source" :disabled="state.busy">
          <option value="library">{{ scopeLabel }}</option><option value="public">公开片目</option>
        </select></label>
        <label>作品类型<select v-model="state.kinds" :disabled="state.busy">
          <option value="auto">按提问识别（默认{{ kind === 'tv' ? '剧集' : '电影' }}）</option>
          <option value="movie">电影</option><option value="tv">剧集</option><option value="both">电影和剧集</option>
        </select></label>
        <label v-if="chips.length" class="inherit"><input v-model="state.inherit" type="checkbox" :disabled="state.busy">沿用当前筛选（{{ chips.length }}）</label>
      </div>
      <p v-if="state.inherit && chips.length" class="dim">{{ chips.map(chip => chip.label).join(' · ') }}</p>
      <div v-if="!state.busy && !state.error" class="examples">
        <JzButton v-for="example in examples" :key="example" variant="ghost" size="compact" @click="$emit('fill', example)">{{ example }}</JzButton>
      </div>
    </div>

    <div v-if="state.busy" class="feedback" role="status" aria-live="polite">
      <Spinner :size="18" />{{ state.refreshing ? '正在核对片单…' : '正在找片…' }}
      <JzButton v-if="!state.refreshing" size="compact" @click="$emit('cancel')">取消</JzButton>
    </div>
    <div v-if="state.error" class="feedback error" role="alert">
      <span>{{ state.error }}</span>
      <JzButton v-if="!state.expired" size="compact" @click="$emit('retry')">重试</JzButton>
      <JzButton v-else size="compact" @click="$emit('restart')">重新找片</JzButton>
      <router-link v-if="['disabled', 'missing_key', 'not_configured', 'invalid_config', 'configuration'].includes(state.errorCode)" :to="{ path: '/settings', query: { sec: 'sec-ai' } }">配置智能辅助</router-link>
      <router-link v-if="state.errorCode === 'tmdb_not_configured'" :to="{ path: '/settings', query: { sec: 'sec-tmdb' } }">配置在线资料服务</router-link>
    </div>

    <template v-if="answer">
      <header class="discover-title"><div>
        <h2>{{ heading }}</h2><p class="dim">{{ state.initialQuestion || state.question }}</p>
        <p v-if="state.initialQuestion && state.initialQuestion !== state.question" class="dim">追问 · {{ state.question }}</p>
      </div><JzButton size="compact" @click="$emit('restart')">重新找片</JzButton></header>
      <p v-if="summary">{{ summary }}</p>
      <p v-if="answer.clarification" class="notice" role="status">{{ answer.clarification }}</p>
      <div v-if="answer.comparison?.length" class="compare"><section v-for="entry in answer.comparison" :key="entry.key || entry.title"><h3>{{ entry.title }}</h3><p>{{ entry.text }}</p></section></div>
      <ul v-if="answer.warnings?.length" class="warnings"><li v-for="message in answer.warnings" :key="message">{{ message }}</li></ul>

      <template v-if="items.length">
        <p class="results-count">{{ answer.source === 'public' ? '公开片目' : scopeLabel }} · {{ items.length }} {{ items.some(hit => ['season', 'episode'].includes(hit.kind)) ? '项' : '部' }}</p>
        <ol class="discover-items">
          <li v-for="(hit, index) in items" :key="hit.key">
            <article class="discover-card">
              <div class="art">
                <img v-if="imageFor(hit)" :src="imageFor(hit)" alt="" loading="lazy" referrerpolicy="no-referrer" @error="failedImages.add(hit.key)">
                <ArtworkPlaceholder v-else label="暂无海报" />
                <span v-if="items.length > 1" class="rank" :aria-label="'第 ' + (index + 1) + ' 部'">{{ index + 1 }}</span>
              </div>
              <div class="copy">
                <h3><router-link v-if="hit.in_library" :to="aiLocalDetailTarget(hit, route)" :data-ai-result-link="hit.key" @click.capture="remember($event, hit)">{{ hit.title }}</router-link><template v-else>{{ hit.title }}</template></h3>
                <p class="meta">{{ [typeLabel[hit.kind], hit.year, scoreFor(hit), watchLabel[hit.watch_state], availabilityFor(hit)].filter(Boolean).join(' · ') }}</p>
                <p v-if="hit.reason || hit.overview" class="reason">{{ hit.reason || hit.overview }}</p>
                <ul v-if="extraEvidence(hit).length" class="evidence"><li v-for="fact in extraEvidence(hit)" :key="fact">{{ fact }}</li></ul>
                <div v-if="!hit.in_library || hit.external_url" class="card-links">
                  <router-link v-if="!hit.in_library" :to="aiOnlineTarget(hit, route)" :data-ai-result-link="hit.key" @click.capture="remember($event, hit)">去在线搜索</router-link>
                  <a v-if="hit.external_url" :href="hit.external_url" target="_blank" rel="noopener noreferrer">来源资料 <AppIcon name="external-link" :size="14" /></a>
                </div>
              </div>
            </article>
          </li>
        </ol>
      </template>
      <EmptyState v-else-if="!state.busy && !answer.clarification && !answer.comparison?.length" state="no-results" title="没有找到符合要求的作品" text="可以补充线索，或重新选择搜索范围。" />
      <div v-if="!state.expired" class="followups">
        <JzButton v-if="answer.source === 'library'" :disabled="state.busy" size="compact" @click="$emit('public')">查公开片目</JzButton>
        <JzButton v-if="items.length && answer.intent !== 'ranking'" :disabled="state.busy" size="compact" icon="refresh" @click="$emit('ask', '换一批')">换一批</JzButton>
        <JzButton v-if="items.length >= 2" :disabled="state.busy" size="compact" @click="$emit('ask', '比较第1部和第2部')">比较前两部</JzButton>
        <JzButton v-if="items.length" size="compact" variant="ghost" @click="$emit('fill', '只换第 1 部')">指定替换一部</JzButton>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { posterUrl } from '../api.js'
import { fmtScore } from '../ratings.js'
import { aiLocalDetailTarget, aiOnlineTarget, rememberAiNavigation } from '../aiDiscoveryNav.js'
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import Spinner from './Spinner.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  state: { type: Object, required: true }, kind: { type: String, required: true },
  scopeLabel: { type: String, default: '当前媒体库' }, chips: { type: Array, default: () => [] },
})
const emit = defineEmits(['cancel', 'retry', 'restart', 'fill', 'ask', 'remember', 'public'])
const route = useRoute()
const failedImages = reactive(new Set())
const answer = computed(() => props.state.result)
const items = computed(() => answer.value?.items || [])
const summary = computed(() => {
  const result = answer.value
  if (!result?.summary || result.summary === result.clarification) return ''
  return result.summary.replace(/^(当前媒体库|TMDB 公开片目) · 共返回 \d+ 项(?: · )?/, '')
})
function extraEvidence(hit) {
  return (hit.evidence || []).filter(fact => !hit.reason?.includes(fact))
}
const typeLabel = { movie: '电影', tv: '剧集', season: '季', episode: '单集' }
const watchLabel = { unseen: '未开始', in_progress: '在看', watched: '已看完', unknown: '观看状态未知' }
const scoreLabel = { tmdb: 'TMDB', douban: '豆瓣', custom: '自评' }
const headings = { identify: '最符合线索的作品', ranking: '片单排行', recommend: '为你推荐', search: '找片结果', compare: '片单比较' }
const heading = computed(() => {
  if (answer.value.status === 'clarification') return '补充一点线索'
  if (answer.value.status === 'comparison') return '片单比较'
  return headings[answer.value.intent] || '找片结果'
})
const examples = computed(() => props.kind === 'tv'
  ? ['今年评分最高的 10 部剧集', '推荐我没看过的慢节奏剧集']
  : ['今年评分最高的 10 部电影', '推荐我没看过的文艺片', '周星驰踢足球的是哪部电影？'])
function availabilityFor(hit) {
  if (answer.value.source !== 'public') return ''
  return hit.in_library ? '本库已收录' : '本库未收录'
}
function scoreFor(hit) {
  const score = fmtScore(hit.rating)
  return score ? [scoreLabel[hit.rating_source] || hit.rating_source, score + ' 分'].filter(Boolean).join(' ') : ''
}
function imageFor(hit) {
  if (failedImages.has(hit.key)) return ''
  return hit.poster_path ? posterUrl(hit.poster_path) : hit.poster_url || ''
}
function remember(event, hit) {
  if (event.button > 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return
  rememberAiNavigation(hit, route)
  emit('remember', hit.key)
}
</script>

<style scoped>
.discover { padding-block: var(--jz-gap-m) var(--jz-gap-2xl); min-width: 0; max-width: 1200px; }
.discover h2, .discover h3, .discover p { margin: 0; }
.discover-title, .discover-scope, .feedback, .examples, .followups, .card-links { display: flex; align-items: center; flex-wrap: wrap; gap: var(--jz-gap-s) var(--jz-gap-m); }
.discover-scope label { display: flex; align-items: center; gap: var(--jz-gap-s); font-size: var(--jz-font-s); }
.discover-scope .inherit { min-height: var(--jz-control-current); cursor: pointer; }
.discover-setup, .discover-title, .feedback, .warnings, .results-count, .discover-items, .compare, .notice { margin-bottom: var(--jz-gap-l); }
.discover .examples, .discover .results-count, .discover .notice, .discover .dim:not(:first-child) { margin-top: var(--jz-gap-s); }
.discover .dim, .discover .meta, .warnings, .evidence, .results-count { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.discover-title { justify-content: space-between; align-items: start; }
.discover-title h2 { font-size: var(--jz-font-xl); }
.feedback { min-height: var(--jz-control-current); }
.error { color: var(--jz-danger); }
.discover-items { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--jz-gap-m) var(--jz-gap-l); }
.discover-card { display: grid; grid-template-columns: clamp(76px, 7vw, 105px) minmax(0, 1fr); align-items: start; gap: var(--jz-gap-m); height: 100%; border-top: 1px solid var(--jz-border); padding-top: var(--jz-gap-m); box-sizing: border-box; }
.art { position: relative; width: 100%; aspect-ratio: 2/3; border-radius: var(--jz-radius-s); overflow: hidden; background: var(--jz-surface-2); }
.art img, .art > div { width: 100%; height: 100%; object-fit: cover; }
.rank { position: absolute; top: 0; left: 0; padding: 2px 7px; border-bottom-right-radius: var(--jz-radius-s); background: var(--jz-surface); color: var(--jz-text); font-weight: 700; }
.copy, .discover-title, .discover p, .compare, .warnings { overflow-wrap: anywhere; }
.copy h3 { font-size: var(--jz-font-l); }
.copy h3 a { color: inherit; }
.copy h3 a, .card-links a { text-decoration-line: underline; text-underline-offset: .2em; }
.copy .reason { margin-top: var(--jz-gap-s); }
.evidence, .warnings { padding-left: 1.3em; }
.evidence { margin: var(--jz-gap-xs) 0; }
.card-links a { display: inline-flex; gap: var(--jz-gap-xs); align-items: center; min-height: var(--jz-control-current); color: var(--jz-link); }
.compare { display: flex; flex-wrap: wrap; gap: var(--jz-gap-m); }
.compare section { flex: 1 1 220px; }
@media (max-width: 700px) {
  .discover-items { grid-template-columns: minmax(0, 1fr); }
  .copy h3 a { display: inline-flex; align-items: center; min-height: var(--jz-touch-target); }
  .discover-scope { align-items: stretch; }
  .discover-scope label:not(.inherit) { flex: 1 1 150px; flex-direction: column; align-items: stretch; }
  .discover-scope select { max-width: 100%; }
}
</style>
