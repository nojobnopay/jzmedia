<script setup>
defineProps({ number: { type: [String, Number], required: true }, title: { type: String, required: true } })
</script>

<template>
  <section class="doc-step" :aria-label="`步骤 ${number}：${title}`">
    <div class="doc-step-heading"><span class="doc-step-number" aria-hidden="true">{{ number }}</span><h2>{{ title }}</h2></div>
    <div class="doc-step-body" :class="{ 'has-image': !!$slots.image }">
      <div class="doc-step-copy"><slot /></div>
      <div v-if="$slots.image" class="doc-step-image"><slot name="image" /></div>
    </div>
  </section>
</template>
