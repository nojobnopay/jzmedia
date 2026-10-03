<template>
  <div class="browse-toolbar" role="search" :aria-label="label">
    <div class="q-wrap">
      <input :id="id + '-input'" name="q" :value="modelValue" :aria-label="label" :placeholder="placeholder"
        role="combobox" aria-autocomplete="list" :aria-expanded="suggestOpen"
        :aria-controls="id + '-suggestions'" :aria-activedescendant="activeOption" autocomplete="off"
        @input="onInput" @compositionstart="$emit('compositionstart', $event)"
        @compositionend="$emit('compositionend', $event)" @keyup.enter="$emit('enter', $event)"
        @keydown.down.prevent="$emit('move', 1)" @keydown.up.prevent="$emit('move', -1)"
        @keydown.esc.stop="$emit('close')" @blur="$emit('blur')" />
      <JzButton class="search-submit" variant="ghost" :aria-label="label" @click="$emit('search')"><AppIcon name="search" :size="19" /></JzButton>
      <ul v-show="suggestOpen" :id="id + '-suggestions'" class="suggest" role="listbox" :aria-label="label + '建议'">
        <li v-if="suggestNoMatch" class="s-empty" role="presentation">无匹配</li>
        <li v-if="suggestItems.length" class="s-head" role="presentation">{{ itemHeading }}</li>
        <li v-for="(item, i) in suggestItems" :id="optionId(i)" :key="'item-' + item.id"
          role="option" :aria-selected="suggestIdx === i" :class="{ on: suggestIdx === i }"
          @mousedown.prevent="$emit('pick-item', item)" @mouseenter="$emit('hover', i)">
          <span class="s-title">{{ item.title }}</span><span v-if="item.year" class="s-year">({{ item.year }})</span>
        </li>
        <li v-if="suggestPersons.length" class="s-head" role="presentation">{{ personHeading }}</li>
        <li v-for="(person, j) in suggestPersons" :id="optionId(suggestItems.length + j)"
          :key="'person-' + (person.tmdb_id || person.name)" role="option"
          :aria-selected="suggestIdx === suggestItems.length + j" :class="{ on: suggestIdx === suggestItems.length + j }"
          @mousedown.prevent="$emit('pick-person', person)" @mouseenter="$emit('hover', suggestItems.length + j)">
          <span class="s-title">{{ person.name }}</span><span class="s-year">库内 {{ person.count }} 部</span>
        </li>
      </ul>
    </div>
    <slot name="filters">
      <JzButton class="browse-tool" :aria-expanded="filtersOpen && !filtersDisabled" :aria-controls="filtersId" :disabled="filtersDisabled" @click="$emit('update:filtersOpen', !filtersOpen)">
        筛选<span v-if="activeCount"> · {{ activeCount }}</span><span aria-hidden="true">{{ filtersOpen ? '⌃' : '⌄' }}</span>
      </JzButton>
    </slot>
    <JzButton class="browse-tool" variant="ghost" :aria-expanded="aiOpen" :aria-controls="id + '-ai'" @click="$emit('update:aiOpen', !aiOpen)">智能搜索</JzButton>
    <slot />
  </div>
</template>
<script setup>
import { computed } from 'vue'
import JzButton from './JzButton.vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  id: { type: String, required: true },
  modelValue: { type: String, default: '' },
  label: { type: String, required: true },
  placeholder: { type: String, default: '' },
  itemHeading: { type: String, default: '影片' },
  personHeading: { type: String, default: '演员' },
  suggestOpen: { type: Boolean, default: false },
  suggestNoMatch: { type: Boolean, default: false },
  suggestItems: { type: Array, default: () => [] },
  suggestPersons: { type: Array, default: () => [] },
  suggestIdx: { type: Number, default: -1 },
  filtersId: { type: String, default: 'browse-filters' },
  filtersOpen: { type: Boolean, default: false },
  filtersDisabled: { type: Boolean, default: false },
  aiOpen: { type: Boolean, default: false },
  activeCount: { type: Number, default: 0 },
})
const emit = defineEmits(['update:modelValue', 'update:filtersOpen', 'update:aiOpen', 'input', 'compositionstart',
  'compositionend', 'enter', 'move', 'close', 'blur', 'pick-item', 'pick-person', 'hover', 'search'])
function optionId(index) { return props.id + '-option-' + index }
const activeOption = computed(() => props.suggestOpen && props.suggestIdx >= 0
  && props.suggestIdx < props.suggestItems.length + props.suggestPersons.length ? optionId(props.suggestIdx) : undefined)
function onInput(event) {
  emit('update:modelValue', event.target.value)
  emit('input', event)
}
</script>
<style scoped>
.browse-toolbar { display: flex; gap: var(--jz-gap-s); flex-wrap: wrap; align-items: center; margin-bottom: var(--jz-gap-l); }
.q-wrap { position: relative; flex: 1 1 280px; max-width: 520px; min-width: 0; }
.q-wrap input { width: 100%; box-sizing: border-box; min-height: var(--jz-control-current); padding-right: 50px; background: var(--jz-surface); }
.search-submit { position: absolute; right: 0; top: 0; min-width: var(--jz-control-current); padding: var(--jz-gap-s); border-radius: 0 var(--jz-radius-s) var(--jz-radius-s) 0; }
.browse-tool[aria-expanded="true"] { background: var(--jz-selected); border-color: var(--jz-accent); color: var(--jz-text); }
.suggest { position: absolute; top: calc(100% + var(--jz-gap-xs)); inset-inline: 0; z-index: 60; list-style: none; margin: 0; padding: var(--jz-gap-xs) 0; max-height: 320px; overflow: auto; background: var(--jz-surface); border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-m); }
.suggest li { padding: var(--jz-gap-s) var(--jz-gap-m); cursor: pointer; display: flex; gap: var(--jz-gap-xs); align-items: baseline; overflow-wrap: anywhere; }
.suggest li.on { background: var(--jz-surface-3); }
.suggest .s-head, .suggest .s-empty { color: var(--jz-text-dim); font-size: var(--jz-font-s); cursor: default; }
.s-title { color: var(--jz-text); }
.s-year { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
@media (max-width: 700px) {
  .browse-toolbar { gap: var(--jz-gap-xs); margin-bottom: var(--jz-gap-m); }
  .q-wrap { flex: 1 1 100%; max-width: none; }
  .browse-tool { padding-inline: var(--jz-gap-m); }
}
</style>
