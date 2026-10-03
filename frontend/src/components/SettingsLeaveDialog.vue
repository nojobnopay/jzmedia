<template>
  <JzDialog :title="busy ? '设置操作尚未完成' : '设置尚未保存'" size="small" data-testid="settings-leave-dialog" @close="emit('stay')">
    <p>{{ busy ? '请等待保存或连接操作结束后再离开。未完成的操作不能通过放弃修改跳过。' : '以下修改还没有保存。继续编辑，或放弃这些修改后离开。' }}</p>
    <ul class="draft-list"><li v-for="(item, index) in items" :key="index"><span>{{ item.label }}</span><JzButton v-if="item.edit && !busy" type="button" @click="edit(item)">继续编辑此项</JzButton></li></ul>
    <template #footer>
      <JzButton ref="stayButton" type="button" variant="primary" @click="emit('stay')">{{ busy ? '留在设置' : '继续编辑' }}</JzButton>
      <JzButton v-if="!busy" type="button" variant="danger" @click="emit('discard')">放弃修改并离开</JzButton>
    </template>
  </JzDialog>
</template>
<script setup>
import { nextTick, onMounted, ref } from 'vue'
import JzDialog from './JzDialog.vue'
import JzButton from './JzButton.vue'
defineProps({ busy: Boolean, items: { type: Array, default: () => [] } })
const emit = defineEmits(['stay', 'discard'])
const stayButton = ref(null)
onMounted(async () => { await nextTick(); stayButton.value?.focus() })
async function edit(item) { emit('stay'); await nextTick(); item.edit() }
</script>
<style scoped>
p { margin: 0 0 16px; }
.draft-list { padding-left: 20px; margin: 0; }
.draft-list li + li { margin-top: 12px; }
.draft-list button { margin-left: 12px; }
</style>
