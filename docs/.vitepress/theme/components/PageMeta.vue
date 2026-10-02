<script setup>
import { useData } from 'vitepress'
const { frontmatter } = useData()
</script>
<template>
  <div v-if="frontmatter.version || frontmatter.reviewed" class="doc-page-meta" aria-label="文档版本">
    <span v-if="frontmatter.version">适用版本 {{ frontmatter.version }}</span>
    <span v-if="frontmatter.reviewed">核对日期 {{ String(frontmatter.reviewed).slice(0, 10) }}</span>
  </div>
</template>
