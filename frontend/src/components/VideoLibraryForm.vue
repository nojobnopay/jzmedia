<template>
  <div class="video-form">
    <fieldset :disabled="busy || disabled"><div class="video-form-fields">
      <label>视频库名称<input v-model="form.name" placeholder="例如：电影" /></label>
      <label>子目录<input v-model="form.subpath" placeholder="相对媒体库根；空=根目录" /></label>
      <label>内容类型<select v-model="form.kind" :disabled="hasContent"><option value="movie">电影</option><option value="tv">剧集</option></select></label>
      </div><p v-if="hasContent" class="hint">库内已有记录，类型不可改。子目录不能与同一存储下的其他视频库重叠。</p>
      <p v-else class="hint">空库可调整类型；电影和剧集应使用独立目录。</p>
      <div class="bar"><JzButton variant="primary" @click="save">{{ busy ? '保存中…' : library.id ? '保存视频库' : '创建视频库' }}</JzButton><JzButton @click="emit('cancel')" type="button">取消</JzButton></div>
    </fieldset>
    <p role="status">{{ error }}</p>
  </div>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useSettingsDraft } from '../settingsDrafts.js'
import { api } from '../api.js'
import JzButton from './JzButton.vue'
const props = defineProps({ library: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['saved', 'cancel', 'busy'])
const form = reactive({ name: '', subpath: '', kind: 'movie' })
const busy = ref(false)
const error = ref('')
const baseline = ref('')
useSettingsDraft({ section: 'sec-libraries', dirty: () => JSON.stringify(form) !== baseline.value,
  busy: () => busy.value, items: () => [{ label: '视频库 · ' + (props.library.name || '新建视频库') }],
  discard: () => { Object.assign(form, JSON.parse(baseline.value)); error.value = '' },
})
const hasContent = computed(() => (props.library.movie_count || 0) + (props.library.episode_count || 0) > 0)
let targetKey = null
watch(() => props.library, value => {
  const key = value.id != null ? `id:${value.id}` : `new:${value.media_library_id}`
  const previous = baseline.value ? JSON.parse(baseline.value) : {}
  const next = { name: value.name || '', subpath: value.subpath || '', kind: value.kind || 'movie' }
  for (const field of Object.keys(next)) {
    if (key !== targetKey || form[field] === previous[field]) form[field] = next[field]
  }
  baseline.value = JSON.stringify(next)
  targetKey = key
}, { immediate: true })
watch(busy, value => emit('busy', value), { flush: 'sync' })
async function save() {
  if (busy.value || props.disabled) return
  busy.value = true; error.value = ''
  try {
    const id = props.library.id
    const result = await api(id ? '/api/libraries/' + id : '/api/libraries', {
      method: id ? 'PATCH' : 'POST',
      body: JSON.stringify({ ...form, ...(id ? {} : { media_library_id: props.library.media_library_id }) }),
    })
    baseline.value = JSON.stringify(form)
    emit('saved', result)
  } catch (e) { error.value = '保存失败：' + e.message }
  finally { busy.value = false }
}
</script>
<style scoped>
fieldset { border: 0; margin: 0; padding: 0; min-width: 0; }
label { display: flex; flex-direction: column; gap: 8px; font-size: var(--jz-font-m); min-width: 0; }
.video-form { width: 100%; }
.video-form-fields { display: grid; grid-template-columns: 1fr 1fr 120px; gap: 16px; }
input, select { width: 100%; min-width: 0; box-sizing: border-box; }
.bar { display: flex; gap: 8px; }
.hint { color: var(--jz-text-dim); font-size: .875rem; }
@media (max-width: 700px) { .video-form-fields { grid-template-columns: minmax(0, 1fr); } }
</style>
