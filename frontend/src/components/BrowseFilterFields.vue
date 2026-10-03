<template>
  <div class="browse-filter-fields">
    <div v-if="group === 'location'" class="filter-mode" role="group" aria-label="产地筛选方式">
      <JzButton v-for="mode in locationModes" :key="mode.value" :aria-pressed="locationMode === mode.value"
        @click="changeLocationMode(mode.value)">{{ mode.label }}</JzButton>
    </div>
    <template v-if="group === 'rating'">
      <fieldset class="filter-fieldset">
        <legend>评分来源</legend>
        <div class="filter-mode">
          <JzButton v-for="source in ratingSources" :key="source.value" :aria-pressed="modelValue.ratingSource === source.value"
            @click="update({ ratingSource: source.value })">{{ source.label }}</JzButton>
        </div>
      </fieldset>
      <p class="filter-field-label">最低评分</p>
      <div class="filter-options" role="group" aria-label="最低评分">
        <JzButton class="filter-option" :aria-pressed="modelValue.rating == null" data-filter-field="rating" data-filter-value=""
          @click="update({ rating: null })">不限</JzButton>
        <JzButton v-for="option in ratingOptions" :key="option.value" class="filter-option" :aria-pressed="modelValue.rating === option.value"
          data-filter-field="rating" :data-filter-value="option.value" @click="update({ rating: option.value })">
          <AppIcon v-if="modelValue.rating === option.value" name="check" :size="15" />
          <span>{{ option.value }} 分以上</span><span v-if="option.count != null" class="filter-option-count" :aria-label="'本库数量 ' + option.count">{{ option.count }}</span>
        </JzButton>
      </div>
      <p class="filter-field-note">设置最低分后，不包含尚无对应评分的{{ kind === 'tv' ? '剧集' : '影片' }}。</p>
    </template>
    <template v-else-if="group === 'watched'">
      <div class="filter-options" role="group" aria-label="观看状态">
        <JzButton class="filter-option" :aria-pressed="modelValue.watched == null" data-filter-field="watched" data-filter-value=""
          @click="update({ watched: null })">不限</JzButton>
        <JzButton v-for="option in watchedOptions" :key="option.value" class="filter-option" :aria-pressed="modelValue.watched === option.value"
          data-filter-field="watched" :data-filter-value="option.value" @click="update({ watched: modelValue.watched === option.value ? null : option.value })">
          <AppIcon v-if="modelValue.watched === option.value" name="check" :size="15" />
          <span>{{ option.label }}</span><span v-if="option.count != null" class="filter-option-count" :aria-label="'本库数量 ' + option.count">{{ option.count }}</span>
        </JzButton>
      </div>
      <p v-if="kind === 'tv'" class="filter-field-note">全部分集已看，才会归入“已看完”。</p>
    </template>
    <template v-else>
      <fieldset v-for="section in sections" :key="section.key" class="filter-fieldset">
        <legend v-if="group === 'period'">{{ section.label }}</legend>
        <label v-if="section.searchable" class="filter-option-search">
          <span class="filter-search-label">{{ section.searchLabel }}</span>
          <input v-model="searches[section.key]" type="search" :aria-label="section.searchLabel" :placeholder="section.searchLabel" autocomplete="off" />
        </label>
        <div v-if="section.options.length" class="filter-options" :class="{ 'filter-options--long': section.searchable }" role="group" :aria-label="section.label">
          <JzButton v-for="option in section.visible" :key="option.value" class="filter-option"
            :aria-pressed="selected(section.key, option.value)" :data-filter-field="section.key" :data-filter-value="option.value"
            @click="toggle(section.key, option.value)">
            <AppIcon v-if="selected(section.key, option.value)" name="check" :size="15" />
            <span>{{ option.label }}</span><span v-if="option.count != null" class="filter-option-count" :aria-label="'本库数量 ' + option.count">{{ option.count }}</span>
          </JzButton>
          <p v-if="!section.visible.length" class="filter-field-note">没有匹配的选项。</p>
        </div>
        <p v-else class="filter-field-note">{{ loading ? '正在加载本库选项…' : error ? '本库选项暂不可用。' : '本库暂无可选项。' }}</p>
        <p v-if="section.hasMissing" class="filter-field-note">已选条件中有本库未列出的项目，仍可点选取消。</p>
      </fieldset>
      <p v-if="group === 'genres' || group === 'status'" class="filter-field-note">可多选，满足任意一项即可。</p>
      <p v-else-if="group === 'location'" class="filter-field-note">地区与国家二选一，切换方式后替换原条件。</p>
      <p v-else-if="group === 'period'" class="filter-field-note">年代与具体年份同时选取时取交集。</p>
      <p v-else-if="group === 'tags'" class="filter-field-note">多选标签时，需要同时包含全部已选标签。</p>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import { normalizeBrowseFilters } from '../browseFilters.js'
import { statusLabel } from '../tvWall.js'

const props = defineProps({
  modelValue: { type: Object, required: true },
  facets: { type: Object, default: () => ({}) },
  kind: { type: String, default: 'movie' },
  group: { type: String, required: true },
  loading: Boolean,
  error: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])
const searches = ref({ countries: '', years: '', tags: '' })
const locationMode = ref(props.modelValue.countries?.length ? 'countries' : 'regions')
const locationModes = [{ value: 'regions', label: '按地区' }, { value: 'countries', label: '按国家' }]
watch(() => props.modelValue, value => {
  if (value.countries?.length) locationMode.value = 'countries'
  else if (value.regions?.length) locationMode.value = 'regions'
})
const ready = computed(() => !props.loading && !props.error)
const ratingSources = computed(() => props.kind === 'tv'
  ? [{ value: 'tmdb', label: 'TMDB' }, { value: 'custom', label: '自评' }]
  : [{ value: 'tmdb', label: 'TMDB' }, { value: 'douban', label: '豆瓣' }, { value: 'custom', label: '自评' }])
const ratingOptions = computed(() => {
  const values = [...new Set([9, 8, 7, 6, ...(props.modelValue.rating == null ? [] : [props.modelValue.rating])])].sort((a, b) => b - a)
  const counts = ready.value ? props.facets.ratings?.[props.modelValue.ratingSource] || [] : []
  return values.map(value => ({ value, count: validCount(counts.find(row => Number(row.min) === value)?.count) }))
})
const watchedOptions = computed(() => [
  { value: 0, label: props.kind === 'tv' ? '未看完' : '未看', count: ready.value ? validCount(props.facets.watched?.unwatched) : null },
  { value: 1, label: props.kind === 'tv' ? '已看完' : '已看', count: ready.value ? validCount(props.facets.watched?.watched) : null },
])
const fieldNames = { genres: '类型', regions: '地区', countries: '国家或地区', decades: '年代', years: '具体年份', tags: '标签', status: '连载状态' }
const searchNames = { countries: '搜索国家或地区', years: '搜索年份', tags: '搜索标签' }
const sections = computed(() => {
  const keys = props.group === 'location' ? [locationMode.value] : props.group === 'period' ? ['decades', 'years'] : [props.group]
  return keys.map(key => {
    const rows = ready.value && Array.isArray(props.facets[key]) ? props.facets[key] : []
    const options = rows.map(row => {
      const value = String(key === 'countries' ? row.code || '未知' : row.value)
      return { value, label: key === 'countries' ? row.name || value : optionLabel(key, value), count: validCount(row.count) }
    })
    const known = new Set(options.map(option => option.value))
    const missing = (props.modelValue[key] || []).filter(value => !known.has(String(value)))
    options.push(...missing.map(value => ({ value: String(value), label: optionLabel(key, String(value)), count: null })))
    const search = String(searches.value[key] || '').trim().toLocaleLowerCase()
    return { key, label: fieldNames[key], options, hasMissing: missing.length > 0 && ready.value,
      searchable: !!searchNames[key] && options.length > 12, searchLabel: searchNames[key],
      visible: options.filter(option => !search || selected(key, option.value) || `${option.label} ${option.value}`.toLocaleLowerCase().includes(search)) }
  })
})
function validCount(value) { return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null }
function optionLabel(key, value) {
  if (key === 'decades') return `${value} 年代`
  if (key === 'status') return statusLabel(value)
  if (key === 'countries') return props.facets.countries?.find(row => String(row.code || '未知') === value)?.name || value
  return value
}
function selected(key, value) { return (props.modelValue[key] || []).includes(value) }
function update(patch) { emit('update:modelValue', normalizeBrowseFilters({ ...props.modelValue, ...patch }, props.kind)) }
function toggle(key, value) {
  const values = props.modelValue[key] || []
  const patch = { [key]: values.includes(value) ? values.filter(item => item !== value) : [...values, value] }
  if (key === 'countries') patch.regions = []
  if (key === 'regions') patch.countries = []
  update(patch)
}
function changeLocationMode(mode) {
  if (mode === locationMode.value) return
  locationMode.value = mode
  update({ countries: [], regions: [] })
}
</script>

<style scoped>
.filter-fieldset { border: 0; min-width: 0; margin: 0 0 var(--jz-gap-l); padding: 0; }
.filter-fieldset:last-of-type { margin-bottom: 0; }
.filter-fieldset legend, .filter-field-label { padding: 0; margin: 0 0 var(--jz-gap-s); font-size: var(--jz-font-m); color: var(--jz-text-dim); }
.filter-mode { display: flex; gap: 4px; padding: 4px; margin-bottom: var(--jz-gap-l); border-radius: var(--jz-radius-m); background: var(--jz-bg); }
.filter-mode .jz-button { flex: 1; min-width: 0; padding-inline: var(--jz-gap-s); border-color: transparent; background: transparent; color: var(--jz-text-dim); }
.filter-mode .jz-button[aria-pressed="true"] { background: var(--jz-surface-3); color: var(--jz-text); }
.filter-options { display: flex; flex-wrap: wrap; gap: var(--jz-gap-s); }
.filter-options--long { max-height: 260px; overflow-y: auto; padding: 4px; margin: -4px; overscroll-behavior: contain; }
.filter-option { gap: 6px; padding-inline: var(--jz-gap-m); max-width: 100%; overflow-wrap: anywhere; }
.filter-option[aria-pressed="true"] { color: var(--jz-text); border-color: var(--jz-danger); background: var(--jz-selected); }
.filter-option-count { color: var(--jz-text-dim); font-size: var(--jz-font-s); font-weight: 400; }
.filter-option :deep(svg) { color: var(--jz-danger); }
.filter-field-note { margin: var(--jz-gap-m) 0 0; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.7; }
.filter-option-search { display: block; margin-bottom: var(--jz-gap-m); }
.filter-search-label { display: block; margin-bottom: var(--jz-gap-xs); font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.filter-option-search input { width: 100%; min-height: var(--jz-control-current); box-sizing: border-box; }
</style>
