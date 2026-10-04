<script setup>
import { useSlots } from 'vue'

const slots = useSlots()
</script>

<template>
  <div class="doc-step">
    <div class="doc-step-body" :class="{ 'has-image': !!slots.image }">
      <div class="doc-step-copy"><slot /></div>
      <div v-if="slots.image" class="doc-step-image"><slot name="image" /></div>
    </div>
  </div>
</template>
