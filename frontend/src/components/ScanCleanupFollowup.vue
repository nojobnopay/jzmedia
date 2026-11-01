<template>
  <section v-if="entry" v-show="!reviewing" class="scan-cleanup-followup" role="region" aria-label="扫描后缓存维护">
    <div class="followup-heading">
      <AppIcon name="image" :size="20" />
      <h4>{{ pending ? '旧缩略图待检查' : '缓存维护已完成' }}</h4>
      <span class="hint">当前视频库：{{ library.label || library.name }}</span>
    </div>
    <p v-if="pending">扫描发现<template v-if="entry.removed"> {{ entry.removed }} 项失效记录</template><template v-if="entry.removed && entry.changed">、</template><template v-if="entry.changed"> {{ entry.changed }} 项片源替换或更新</template>，可能留下旧缩略图。先完成整理与新版预览生成，再检查可回收空间。</p>
    <p v-else role="status">{{ completion }}</p>
    <div class="followup-actions">
      <ObsoletePreviewCleanup :dialog="true" :label="pending ? '检查废弃缩略图' : '重新检查废弃缩略图'"
        :context-name="library.label || library.name" :disabled="disabled"
        @busy-change="checking" @checked="checked" @cleaned="cleaned" @generate-previews="openPreviews" />
      <RouterLink v-if="entry.changed" :to="previewTarget">生成新版缩略图</RouterLink>
      <RouterLink :to="{ path: '/settings', query: { sec: 'sec-index' } }">系统维护</RouterLink>
    </div>
    <p v-if="pending" class="hint">检查与清理作用于全部媒体库，确认后才删除废弃图片；受保护的缓存会保留。提醒保存在当前浏览器，稍后返回本库仍可继续。</p>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { scanCleanupFollowups } from '../scanCleanupFollowups.js'
import { tvIngestFlow } from '../useTvIngestFlow.js'
import { fmtBytes } from '../format.js'
import AppIcon from './AppIcon.vue'
import ObsoletePreviewCleanup from './ObsoletePreviewCleanup.vue'

const props = defineProps({ library: { type: Object, required: true }, disabled: Boolean })
const router = useRouter()
const completedEntry = ref(null), completion = ref('')
const pending = computed(() => scanCleanupFollowups.state.value.entries[props.library.id] || null)
const entry = computed(() => pending.value || completedEntry.value)
const reviewing = computed(() => Boolean(tvIngestFlow.review.value || tvIngestFlow.queue.value.length))
const previewTarget = computed(() => ({ path: '/settings', query: { sec: 'sec-meta', library: props.library.id } }))
let reviewed = {}
function checking(busy) { if (busy) reviewed = scanCleanupFollowups.snapshot() }
function checked(result) {
  if (result.candidates !== 0) return
  const original = entry.value
  if (scanCleanupFollowups.settle(result, reviewed)) {
    completedEntry.value = original
    completion.value = '已检查，当前没有可清理的废弃缩略图。'
  }
}
function cleaned(result) {
  const original = entry.value
  if (scanCleanupFollowups.settle(result, reviewed)) {
    completedEntry.value = original
    completion.value = `已清理 ${result.removed} 个废弃目录，释放 ${fmtBytes(result.freed)}。`
  }
}
function openPreviews() { router.push(previewTarget.value) }
</script>

<style scoped>
.scan-cleanup-followup { margin: 0 0 20px; padding: 16px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); background: var(--jz-surface); }
.followup-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.followup-heading h4 { margin: 0; font-size: var(--jz-font-m); }
.scan-cleanup-followup p { margin: 10px 0 0; line-height: 1.6; overflow-wrap: anywhere; }
.followup-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-top: 12px; }
.followup-actions :deep(.obsolete-cleanup) { margin-top: 0; }
.followup-actions a { display: inline-flex; align-items: center; min-height: 40px; color: var(--jz-text-dim); text-decoration: underline; text-underline-offset: 3px; }
.followup-actions a:hover { color: var(--jz-text); }
.hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
@media (max-width: 700px) { .followup-actions a { min-height: 44px; } }
</style>
