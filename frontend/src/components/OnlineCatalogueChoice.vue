<template>
  <JzField :id="id" label="新地址的片库" hint="同一片库会沿用收藏、观看进度和缓存。更换片库会创建独立来源并停用旧来源；旧记录保留，已缓存分集仍可在本地缓存中观看。" v-slot="field">
    <select :id="field.id" v-model="choice" required :disabled="disabled" :aria-describedby="field.describedby">
      <option disabled value="">请选择片库关系</option>
      <option value="mirror">同一片库，更换镜像地址</option>
      <option value="replace">更换为另一片库</option>
    </select>
  </JzField>
</template>

<script setup>
import JzField from './JzField.vue'

defineProps({ id: { type: String, required: true }, disabled: Boolean })
const choice = defineModel({ type: String, default: '' })
</script>
