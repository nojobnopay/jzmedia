<template>
  <Teleport to="body">
    <div v-if="open" class="file-review-mask" @keydown.esc.stop.prevent="!scanning && emit('stay')">
      <section ref="box" class="file-review-dialog" role="dialog" aria-modal="true" aria-labelledby="file-review-title">
        <h2 id="file-review-title">文件变更尚未核对</h2>
        <p v-if="change?.count">“{{ libraryName }}”库有 {{ change.count }} 项文件变更，建议扫描后核对入库与匹配结果。</p>
        <p v-else>请先确认“{{ libraryName }}”库的文件操作状态，完成变更后再扫描核对。</p>
        <p v-if="working">文件操作仍在进行。请等待完成后扫描；稍后处理会保留待扫描提醒。</p>
        <p v-if="scanBlocked && !working">已有扫描任务正在运行，结束后可扫描这个视频库。</p>
        <p v-if="error" role="alert">{{ error }}</p>
        <p class="hint">改名、移动保留已有匹配与观看记录；扫描结果中的未匹配和集号待办仍需核对。</p>
        <div class="file-review-actions">
          <button class="primary" :disabled="working || scanBlocked || scanning" @click="emit('scan')">{{ scanning ? '正在启动扫描…' : '扫描并核对' }}</button>
          <button :disabled="scanning" @click="emit('later')">稍后处理</button>
          <button :disabled="scanning" @click="emit('stay')">继续管理</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useFocusTrap } from '../useFocusTrap.js'
const props = defineProps({
  open: Boolean, libraryName: String, change: Object, working: Boolean,
  scanning: Boolean, scanBlocked: Boolean, error: String,
})
const emit = defineEmits(['scan', 'later', 'stay'])
const box = ref(null)
useFocusTrap(computed(() => props.open), box)
</script>
<style scoped>
.file-review-mask { position: fixed; inset: 0; z-index: 1600; background: #000a; display: flex; align-items: center; justify-content: center; padding: 20px; }
.file-review-dialog { width: 560px; max-width: 100%; max-height: 85vh; overflow: auto; padding: 24px; box-sizing: border-box; border: 1px solid var(--jz-border-strong); border-radius: var(--jz-radius-l); background: var(--jz-surface); box-shadow: 0 16px 60px #0008; }
h2 { font-size: 1.25rem; margin-top: 0; }
p { line-height: 1.7; }
.hint { color: var(--jz-text-dim); font-size: .875rem; }
.file-review-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; }
.file-review-actions button { min-height: 42px; }
.primary { background: var(--jz-accent); border-color: var(--jz-accent); color: white; }
</style>
