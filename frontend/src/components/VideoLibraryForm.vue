<template>
  <div class="video-form">
    <fieldset :disabled="busy || disabled">
      <label>视频库名称<input v-model="form.name" placeholder="例如：电影" /></label>
      <label>子目录<input v-model="form.subpath" placeholder="相对媒体库根；空=根目录" /></label>
      <label>内容类型<select v-model="form.kind" :disabled="hasContent"><option value="movie">电影</option><option value="tv">剧集</option></select></label>
      <p v-if="hasContent" class="hint">库内已有记录，类型不可改。子目录不能与同一存储下的其他视频库重叠。</p>
      <p v-else class="hint">空库可调整类型；电影和剧集应使用独立目录。</p>
      <div class="bar"><button @click="save">{{ busy ? '保存中…' : library.id ? '保存视频库' : '创建视频库' }}</button><button @click="emit('cancel')">取消</button></div>
    </fieldset>
    <p role="status">{{ error }}</p>
  </div>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../api.js'
const props = defineProps({ library: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['saved', 'cancel', 'busy'])
const form = reactive({ name: '', subpath: '', kind: 'movie' })
const busy = ref(false)
const error = ref('')
const hasContent = computed(() => (props.library.movie_count || 0) + (props.library.episode_count || 0) > 0)
watch(() => props.library, value => {
  form.name = value.name || ''; form.subpath = value.subpath || ''; form.kind = value.kind || 'movie'
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
    emit('saved', result)
  } catch (e) { error.value = '保存失败：' + e.message }
  finally { busy.value = false }
}
</script>
<style scoped>
fieldset { border: 0; margin: 0; padding: 0; min-width: 0; }
label { display: inline-flex; flex-direction: column; gap: 6px; margin: 6px 12px 6px 0; max-width: 100%; }
input { box-sizing: border-box; max-width: 100%; }
.bar { display: flex; gap: 8px; }
.hint { color: var(--jz-text-dim); font-size: .875rem; }
</style>
