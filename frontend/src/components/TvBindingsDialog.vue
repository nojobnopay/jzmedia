<template>
  <JzDialog class="binding-dialog" mask-class="binding-mask" size="large" labelledby="tv-binding-title" :busy="applying" @close="close">
        <template #header>
          <div><h2 id="tv-binding-title">剧集归属与季号</h2><p class="hint">选择本地目录，确认它们属于哪部剧、哪一季。保存后新增分集和重新扫描会沿用。</p><HelpLink page="user-guide/tv-bindings" label="目录归属与季号图解" /></div>
        </template>
        <p v-if="loading" role="status">正在读取已扫描目录与现有归属…</p>
        <p v-if="refreshing" role="status" class="hint">已显示数据库中的目录，正在后台刷新磁盘目录…</p>
        <p v-if="error" class="binding-error" role="alert">{{ error }}</p>
        <p v-if="completed" class="binding-success" role="status">
          {{ completed.undone ? '已撤销归属变更，观看记录保留。' : `已保存 ${completed.directories} 个目录的归属，共 ${completed.episodes} 个视频。原目录和文件名保持不变。` }}
          <router-link v-if="completed.show_id && !completed.undone" :to="'/tv/' + completed.show_id" @click="close">查看剧集</router-link>
        </p>
        <fieldset :disabled="applying || loading">
          <legend>1. 选择目录</legend>
          <input v-model="filter" class="binding-filter" placeholder="筛选目录名" aria-label="筛选目录名" />
          <span class="hint">已选 {{ selected.length }} 个目录</span>
          <div class="binding-directories">
            <label v-for="row in filteredRows" :key="row.path" class="binding-directory">
              <input v-model="row.checked" type="checkbox" />
              <span><strong>{{ row.path }}</strong><small>{{ row.files }} 个视频 · {{ row.episodes.length }} 个集号<template v-if="row.binding"> · 已确认归属</template></small></span>
            </label>
            <p v-if="!loading && !refreshing && !filteredRows.length" class="hint">没有符合条件的剧集目录。</p>
          </div>
        </fieldset>
        <fieldset :disabled="applying || loading">
          <legend>2. 选择目标剧集</legend>
          <div class="binding-bar">
            <input v-model="query" placeholder="输入完整剧名或系列名" aria-label="搜索目标剧集" @keydown.enter.prevent="search" />
            <JzButton icon="search" type="button" :disabled="searching" @click="search">{{ searching ? '搜索中…' : '搜索' }}</JzButton>
            <JzButton icon="match" type="button" :disabled="!selected.length || selected.length > 12 || suggesting" @click="suggest()">{{ suggesting ? '查询季资料…' : '按目录获取建议' }}</JzButton>
          </div>
          <label v-if="matchedShows.length" class="binding-local">或选择库中已有剧集
            <select aria-label="库中已有目标剧集" :value="target?.show_id || ''" @change="chooseLocal($event.target.value)">
              <option value="">请选择</option>
              <option v-for="s in matchedShows" :key="s.id" :value="s.id">{{ s.title }} {{ s.year || '' }}</option>
            </select>
          </label>
          <div v-for="r in results" :key="r.tmdb_id" class="binding-result">
            <span>{{ r.title }} {{ r.year || '' }}</span><JzButton type="button" @click="selectResult(r)">选择并查看季</JzButton>
          </div>
          <p v-for="note in notes" :key="note" class="hint">{{ note }}</p>
          <div v-for="c in candidates" :key="c.tmdb_id" class="binding-candidate">
            <div class="binding-bar"><strong>{{ c.title }} {{ c.year || '' }}</strong><span v-if="c.offline" class="hint">缓存资料</span>
              <JzButton type="button" @click="choose(c, true)">采用此目标与季号建议</JzButton></div>
            <p class="hint">{{ c.seasons.map(s => `第 ${s.season_number} 季 · ${s.name}（${s.episode_count} 集）`).join(' / ') }}</p>
            <p v-for="d in c.directories" :key="d.path" class="binding-evidence">
              {{ d.path }}：<template v-if="d.suggestions.length">建议第 {{ d.suggestions[0].season }} 季；{{ d.suggestions[0].reasons.join('；') }}</template><template v-else>缺少足够证据，请手动选择季号</template>
            </p>
          </div>
          <p v-if="target" class="binding-target">目标：<strong>{{ target.title }}</strong> {{ target.year || '' }}<span v-if="target.show_id"> · 合并到已有剧集</span></p>
        </fieldset>
        <fieldset v-if="selected.length && target" :disabled="applying">
          <legend>3. 核对每个目录的季号</legend>
          <div v-for="row in selected" :key="row.path" class="binding-season-row">
            <strong>{{ row.path }}</strong>
            <label>目标季 <select v-model="row.season" :aria-label="row.path + '目标季'">
              <option value="">保留原季号</option>
              <template v-if="target.seasons?.length"><option v-for="s in target.seasons" :key="s.season_number" :value="s.season_number">第 {{ s.season_number }} 季 · {{ s.name }}</option></template>
              <template v-else><option v-for="n in 100" :key="n - 1" :value="n - 1">{{ n === 1 ? '特典（第 0 季）' : `第 ${n - 1} 季` }}</option></template>
            </select></label>
            <label><input v-model="row.override" type="checkbox" />允许覆盖文件中明确的季号</label>
          </div>
          <p class="hint">只指定季号，不改变集号。特典仍保留第 0 季；剪辑版、连续编号请先核对实际内容。</p>
          <details><summary>处理已有绑定与重复集号</summary>
            <label class="binding-check"><input v-model="replaceManual" type="checkbox" />允许替换受影响的手工分集绑定或“本地集”确认</label>
            <label class="binding-check"><input v-model="allowDuplicates" type="checkbox" />我已核对重复集号，保留全部文件作为多个版本</label>
          </details>
          <JzButton icon="eye" type="button" class="primary" variant="primary" :disabled="!canPreview || previewing" @click="preview">{{ previewing ? '正在核对…' : '预览归属变更' }}</JzButton>
        </fieldset>
        <section v-if="plan" class="binding-preview" aria-live="polite">
          <h3>确认预览 · {{ plan.target.title }}</h3>
          <p>{{ plan.episodes }} 个视频 · {{ plan.matched }} 个有对应分集资料 · {{ plan.extras }} 个已登记花絮跟随归属</p>
          <div v-for="g in plan.groups" :key="g.path" class="binding-group">
            <strong>{{ g.path }}</strong><p>{{ g.files }} 个视频 → {{ g.season === null ? '保留原季号' : `第 ${g.season} 季` }}</p>
            <div v-for="sample in g.samples" :key="sample.file" class="hint">{{ sample.file }}：{{ episodeLabel(sample.before) }} → {{ episodeLabel(sample.after) }}</div>
          </div>
          <p v-for="(c, i) in plan.conflicts" :key="'c' + i" class="binding-error">{{ c }}</p>
          <p v-for="(w, i) in plan.warnings" :key="'w' + i" class="binding-warning">{{ w }}</p>
          <details v-if="plan.duplicate_count"><summary>查看重复集号（{{ plan.duplicate_count }}）</summary>
            <p v-for="d in plan.duplicates" :key="d.season + ':' + d.episode">{{ episodeLabel(d) }}：{{ d.files.join(' / ') }}</p>
          </details>
          <p class="hint">确认将更新剧集归属和资料，保留观看记录。文件整理可在剧详情另行预览。</p>
          <JzButton type="button" class="primary" variant="primary" :disabled="!canApply" @click="apply">{{ applying ? '正在保存…' : '确认并保存归属' }}</JzButton>
        </section>
        <details class="binding-history"><summary>归属变更记录（{{ history.length }}）</summary>
          <div v-for="h in history" :key="h.id" class="binding-group">
            <strong>{{ h.title }}</strong> · {{ h.episodes }} 个视频 · {{ new Date(h.created_at * 1000).toLocaleString() }}
            <p class="hint">{{ h.directories.join(' / ') }}</p>
            <JzButton icon="eye" v-if="h.state === 'applied'" type="button" :disabled="applying" @click="previewUndo(h)">预览撤销</JzButton><span v-else>已撤销</span>
          </div>
        </details>
        <section v-if="undoPlan" class="binding-preview">
          <h3>撤销“{{ undoPlan.title }}”的归属变更</h3>
          <p>恢复 {{ undoPlan.episodes }} 个视频原有归属和季集号，保留现在的观看记录。</p>
          <p v-for="d in undoPlan.directories" :key="d" class="hint">{{ d }}</p>
          <JzButton type="button" :disabled="applying" @click="undo">确认撤销</JzButton>
          <JzButton type="button" :disabled="applying" @click="invalidate">取消</JzButton>
        </section>
  </JzDialog>
</template>

<script setup>
import HelpLink from './HelpLink.vue'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import { computed, onMounted, ref } from 'vue'
import { useTvBindings } from '../useTvBindings.js'
const props = defineProps({ libraryId: { type: Number, required: true }, showId: { type: Number, default: null } })
const emit = defineEmits(['close', 'changed'])
const filter = ref('')
const { rows, shows, history, query, results, candidates, target, plan, undoPlan, completed,
  error, notes, loading, refreshing, searching, suggesting, previewing, applying, replaceManual, allowDuplicates,
  selected, canPreview, canApply, load, search, suggest, choose, preview, apply, previewUndo, undo,
  invalidate } = useTvBindings(props.libraryId, props.showId, { onChanged: result => emit('changed', result) })
const filteredRows = computed(() => rows.value.filter(r => r.path.toLowerCase().includes(filter.value.toLowerCase())))
const matchedShows = computed(() => shows.value.filter(s => s.tmdb_id))
function close() { if (!applying.value) emit('close') }
function chooseLocal(value) {
  const s = shows.value.find(s => s.id === Number(value))
  if (s) { choose({ ...s, show_id: s.id }); suggest(s.tmdb_id) }
}
function selectResult(row) { choose(row); suggest(row.tmdb_id) }
function episodeLabel(e) {
  const pad = n => String(n).padStart(2, '0')
  return `S${pad(e.season)}E${pad(e.episode)}${e.episode_end > e.episode ? `–E${pad(e.episode_end)}` : ''}`
}
onMounted(load)
</script>

<style scoped>
.binding-bar,.binding-result{display:flex;align-items:center;gap:12px;justify-content:space-between}
fieldset{border:1px solid var(--jz-border-strong);border-radius:var(--jz-radius-m);margin:18px 0;padding:16px;min-width:0}legend{font-weight:600;padding:0 8px}.binding-filter{margin-right:12px;margin-bottom:10px;max-width:100%}
.binding-directories{max-height:230px;overflow:auto}.binding-directory{display:flex;align-items:flex-start;gap:12px;padding:10px 4px;cursor:pointer}.binding-directory span{min-width:0}.binding-directory strong{overflow-wrap:anywhere}.binding-directory small{display:block;color:var(--jz-text-dim);margin-top:4px}
.binding-bar{justify-content:flex-start;flex-wrap:wrap}.binding-bar input{flex:1;min-width:180px}.binding-local{display:flex;gap:12px;align-items:center;margin:12px 0}.binding-result{padding:8px 0}.binding-candidate,.binding-group{padding:12px;margin:10px 0;border:1px solid var(--jz-border-strong);border-radius:var(--jz-radius-s);overflow-wrap:anywhere}.binding-evidence{font-size:13px;color:var(--jz-text-dim)}.binding-target{background:var(--jz-surface-2);padding:12px;border-radius:var(--jz-radius-s)}
.binding-season-row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;padding:12px 0;border-bottom:1px solid var(--jz-border)}.binding-season-row strong{width:100%;overflow-wrap:anywhere}.binding-season-row label{display:flex;gap:8px;align-items:center}.binding-check{display:block;margin:12px 0}details{margin:12px 0}summary{cursor:pointer}fieldset>.primary{margin-top:12px}.binding-preview{border:1px solid var(--jz-border-strong);border-radius:var(--jz-radius-m);padding:16px;margin:16px 0}.binding-error{color:var(--jz-danger);overflow-wrap:anywhere}.binding-warning{color:var(--jz-warn);overflow-wrap:anywhere}.binding-success{color:var(--jz-green)}.hint{color:var(--jz-text-dim);font-size:13px}.binding-history{margin-top:24px}
@media(max-width:600px){.binding-bar input{min-width:100%;box-sizing:border-box}.binding-local{align-items:flex-start;flex-direction:column}.binding-directories{max-height:180px}fieldset{padding:10px}.binding-season-row label{font-size:13px}}
</style>
