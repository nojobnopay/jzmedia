<template>
  <div v-if="(plan.untouched || []).length" class="note-block">
    <div class="note-title warn">未整理的花絮目录（{{ plan.untouched_count }} 个文件，层级较深）</div>
    <div v-for="(t, i) in plan.untouched" :key="'u' + i" class="note-line">{{ untouchedText(t) }}</div>
  </div>
  <div v-if="(plan.kept || []).length" class="note-block">
    <div class="note-title">保持原名（已确认本地集，{{ plan.kept.length }}）</div>
    <div v-for="(k, i) in plan.kept" :key="'k' + i" class="note-line" :title="k">{{ basename(k) }}</div>
  </div>
  <div v-if="(plan.manual || []).length" class="note-block">
    <div class="note-title warn">需手动处理（{{ plan.manual.length }}{{ plan.manual_more ? `+${plan.manual_more}` : '' }}）</div>
    <div v-for="(m, i) in plan.manual" :key="'m' + i" class="note-line" :title="m.suggestion">{{ manualText(m) }}</div>
    <div v-if="plan.manual_more" class="note-more">还有 {{ plan.manual_more }} 条同样需手动处理</div>
  </div>
  <div v-if="(plan.conflicts || []).length" class="note-block">
    <div class="note-title warn">冲突（不会执行，{{ plan.conflicts.length }}）</div>
    <div v-for="(c, i) in plan.conflicts" :key="'c' + i" class="note-line">
      {{ basename(c.from) }} → {{ basename(c.to) }}：{{ c.reason }}
    </div>
  </div>
  <div v-if="(plan.warnings || []).length" class="note-block">
    <div class="note-title warn">提示</div>
    <div v-for="(w, i) in plan.warnings" :key="'w' + i" class="note-line">{{ w }}</div>
  </div>
</template>
<script setup>
import { basename, manualText, untouchedText } from '../tvOrganizePlans.js'

defineProps({ plan: { type: Object, required: true } })
</script>
<style scoped>
.note-block { margin: 8px 0; }
.note-title { color: #bbb; font-size: 0.8125rem; }
.note-title.warn { color: #e0a63c; }
.note-line { color: #8a8a8a; font-size: 0.8125rem; padding-left: 12px; overflow-wrap: anywhere; }
.note-more { color: #666; font-size: 0.8125rem; padding-left: 12px; }
</style>
