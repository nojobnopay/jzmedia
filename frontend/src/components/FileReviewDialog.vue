<template>
  <JzDialog :open="open" class="file-review-dialog" mask-class="file-review-mask" size="small" layer="preview"
    title="文件变更尚未核对" :busy="scanning" :close-on-backdrop="false" close-label="关闭文件核对提示" @close="emit('stay')">
    <p v-if="change?.count">“{{ libraryName }}”库有 {{ change.count }} 项文件变更，建议扫描后核对入库与匹配结果。</p>
    <p v-else>请先确认“{{ libraryName }}”库的文件操作状态，完成变更后再扫描核对。</p>
    <p v-if="working" role="status">文件操作仍在进行。请等待完成后扫描；稍后处理会保留待扫描提醒。</p>
    <p v-if="scanBlocked && !working" role="status">已有扫描任务正在运行，结束后可扫描这个视频库。</p>
    <p v-if="error" class="file-review-error" role="alert">{{ error }}</p>
    <p class="hint">改名、移动保留已有匹配与观看记录；扫描结果中的未匹配和集号待办仍需核对。</p>
    <template #footer>
      <JzButton variant="primary" :disabled="working || scanBlocked" :loading="scanning" @click="emit('scan')">{{ scanning ? '正在启动扫描…' : '扫描并核对' }}</JzButton>
      <JzButton :disabled="scanning" @click="emit('later')">稍后处理</JzButton>
      <JzButton :disabled="scanning" @click="emit('stay')">继续管理</JzButton>
    </template>
  </JzDialog>
</template>
<script setup>
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
defineProps({
  open: Boolean, libraryName: String, change: Object, working: Boolean,
  scanning: Boolean, scanBlocked: Boolean, error: String,
})
const emit = defineEmits(['scan', 'later', 'stay'])
</script>
<style scoped>
p { margin: 0 0 16px; line-height: 1.7; }
p:last-child { margin-bottom: 0; }
.hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.file-review-error { color: var(--jz-danger); overflow-wrap: anywhere; }
</style>
