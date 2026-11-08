<script setup>
import { computed } from 'vue'

const props = defineProps({
  kind: { type: String, required: true },
  frame: { type: Object, required: true },
})

const directories = computed(() => {
  if (props.kind !== 'tree') return []
  if (props.frame.state === 'preview') return [
    { label: '当前位置', data: props.frame.original, planned: false },
    { label: '预览目标 · 尚未执行', data: props.frame, planned: true },
  ]
  return [{ label: props.frame.state === 'after' ? '整理后的目录' : '当前目录', data: props.frame, planned: false }]
})

// Break at filename separators first; season/episode tokens remain readable.
function pathParts(value) {
  return value.match(/[^./-]+[./-]?|[./-]/g) || [value]
}
function keepToken(value) {
  return /^[\x00-\x7F]{1,24}$/.test(value)
}
</script>

<template>
  <div v-if="kind === 'binding'" class="doc-binding-map" :class="`is-${frame.state}`">
    <div class="doc-binding-columns" aria-hidden="true">
      <span>文件所在目录</span>
      <span></span>
      <span>应用中的归属</span>
    </div>
    <ol class="doc-binding-rows" role="list">
      <li v-for="(folder, index) in frame.folders" :key="folder.name" class="doc-binding-row">
        <div class="doc-binding-source" role="group" aria-label="文件所在目录">
          <span class="doc-binding-mobile-label" aria-hidden="true">文件所在目录</span>
          <p class="doc-binding-name">{{ folder.name }}</p>
          <code class="doc-binding-file">{{ folder.file }}</code>
        </div>
        <span class="doc-binding-connector" aria-hidden="true">{{ frame.state === 'review' ? '…' : '→' }}</span>
        <div class="doc-binding-target" role="group" aria-label="应用中的归属">
          <span class="doc-binding-mobile-label" aria-hidden="true">应用中的归属</span>
          <p class="doc-binding-name">{{ frame.targets[index].name }}</p>
          <p class="doc-binding-description">{{ frame.targets[index].detail }}</p>
        </div>
      </li>
    </ol>
    <p v-if="frame.state === 'saved'" class="doc-binding-saved">目录规则已保存</p>
  </div>

  <div v-else-if="kind === 'tree'" class="doc-directory-comparison" :class="{ 'is-preview': frame.state === 'preview' }">
    <section
      v-for="directory in directories"
      :key="directory.label"
      class="doc-directory"
      :class="{ 'is-planned': directory.planned }"
      :aria-label="directory.label"
    >
      <p class="doc-directory-label">{{ directory.label }}</p>
      <div class="doc-directory-tree">
        <p class="doc-directory-root">{{ directory.data.root }}</p>
        <div class="doc-directory-branch">
          <p class="doc-directory-season">{{ directory.data.season }}</p>
          <ul class="doc-directory-files" role="list">
            <li v-for="file in directory.data.files" :key="file">
              <code class="doc-directory-file"><template v-for="(part, index) in pathParts(file)" :key="index"><span :class="{ 'doc-path-token': keepToken(part) }">{{ part }}</span><wbr></template></code>
            </li>
          </ul>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.doc-binding-map, .doc-directory-comparison { margin: 28px 0; }
.doc-binding-columns, .doc-binding-row { display: grid; grid-template-columns: minmax(0, 1fr) 48px minmax(0, 1.15fr); column-gap: 20px; align-items: center; }
.doc-binding-columns { margin-bottom: 6px; color: var(--vp-c-text-2); font-size: 13px; }
.doc-binding-rows { padding: 0; margin: 0; list-style: none; }
.doc-binding-row { margin: 0; padding: 20px 0; border-bottom: 1px solid var(--vp-c-divider); }
.doc-binding-source, .doc-binding-target { min-width: 0; }
.doc-binding-name { margin: 0 0 4px; font-size: 18px; line-height: 1.65; font-weight: 600; overflow-wrap: anywhere; }
.doc-binding-file { padding: 0; background: none; color: var(--vp-c-text-2); font-size: 15px; line-height: 1.8; overflow-wrap: anywhere; }
.doc-binding-description { margin: 0; color: var(--vp-c-text-2); font-size: 15px; line-height: 1.8; }
.doc-binding-connector { color: var(--vp-c-brand-1); font-size: 26px; text-align: center; line-height: 1; }
.is-review .doc-binding-connector, .is-review .doc-binding-target { color: var(--vp-c-text-2); }
.is-review .doc-binding-target .doc-binding-name { font-weight: 400; }
.doc-binding-mobile-label { display: none; }
.doc-binding-saved { margin: 12px 0 0; color: var(--vp-c-brand-1); font-size: 14px; line-height: 1.8; }
.doc-directory-comparison { display: grid; grid-template-columns: minmax(0, 1fr); gap: 32px; }
.doc-directory-comparison.is-preview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.doc-directory { min-width: 0; }
.doc-directory-label { margin: 0 0 16px; color: var(--vp-c-text-2); font-size: 13px; line-height: 1.7; }
.doc-directory-tree { font-size: 16px; line-height: 1.8; }
.doc-directory-root { margin: 0 0 12px; font-weight: 600; }
.doc-directory-branch { margin-left: 8px; border-left: 1px solid var(--vp-c-divider); padding-left: 20px; }
.doc-directory-season { position: relative; margin: 0 0 8px; font-weight: 600; }
.doc-directory-season::before { content: ''; position: absolute; top: .9em; left: -20px; width: 12px; border-top: 1px solid var(--vp-c-divider); }
.doc-directory-files { margin: 0 0 0 8px; padding: 0 0 0 20px; border-left: 1px solid var(--vp-c-divider); list-style: none; }
.doc-directory-files li { position: relative; margin: 0; padding: 5px 0; }
.doc-directory-files li::before { content: ''; position: absolute; top: 1.2em; left: -20px; width: 12px; border-top: 1px solid var(--vp-c-divider); }
.doc-directory-file { padding: 0; background: none; color: var(--vp-c-text-1); font-size: 15px; line-height: 1.85; overflow-wrap: anywhere; }
.doc-path-token { white-space: nowrap; }
.is-planned .doc-directory-label { color: var(--vp-c-brand-1); }
.is-planned .doc-directory-branch, .is-planned .doc-directory-files { border-left-style: dashed; }
.is-planned .doc-directory-season::before, .is-planned .doc-directory-files li::before { border-top-style: dashed; }
@media (max-width: 640px) {
  .doc-binding-map, .doc-directory-comparison { margin: 24px 0; }
  .doc-binding-columns { display: none; }
  .doc-binding-row { grid-template-columns: 24px minmax(0, 1fr); column-gap: 12px; row-gap: 12px; padding: 20px 0; }
  .doc-binding-row:first-child { padding-top: 0; }
  .doc-binding-source { grid-column: 2; }
  .doc-binding-connector { grid-column: 1; grid-row: 2; transform: rotate(90deg); font-size: 22px; }
  .doc-binding-target { grid-column: 2; }
  .doc-binding-mobile-label { display: block; margin-bottom: 3px; color: var(--vp-c-text-2); font-size: 12px; }
  .doc-binding-name { font-size: 17px; }
  .doc-binding-description, .doc-binding-file { font-size: 15px; }
  .doc-directory-comparison.is-preview { grid-template-columns: minmax(0, 1fr); gap: 28px; }
  .doc-directory-label { margin-bottom: 12px; }
  .doc-directory-tree { font-size: 15px; }
  .doc-directory-branch { margin-left: 5px; padding-left: 16px; }
  .doc-directory-season::before { left: -16px; width: 10px; }
  .doc-directory-files { margin-left: 5px; padding-left: 16px; }
  .doc-directory-files li::before { left: -16px; width: 10px; }
}
</style>
