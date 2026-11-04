<template>
  <div class="jz-field">
    <label :for="id">{{ label }}</label>
    <div v-if="$slots.actions" class="jz-field-control-row">
      <div class="jz-field-control"><slot :id="id" :describedby="descriptionId" :invalid="!!error" /></div>
      <div class="jz-field-actions"><slot name="actions" /></div>
    </div>
    <slot v-else :id="id" :describedby="descriptionId" :invalid="!!error" />
    <p v-if="error || hint" :id="descriptionId" class="jz-field-description" :class="{ 'is-error': error }" :role="error ? 'alert' : undefined">{{ error || hint }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  id: { type: String, required: true },
  label: { type: String, required: true },
  hint: { type: String, default: '' },
  error: { type: String, default: '' },
})
const descriptionId = computed(() => props.error || props.hint ? `${props.id}-description` : undefined)
</script>

<style scoped>
.jz-field { display: grid; align-content: start; gap: var(--jz-gap-s); min-width: 0; margin-block: var(--jz-gap-l); }
.jz-field > label { margin: 0; color: var(--jz-text); font-size: var(--jz-font-m); font-weight: 500; line-height: var(--jz-line-height); }
.jz-field :deep(input), .jz-field :deep(select), .jz-field :deep(textarea) { width: 100%; }
.jz-field-control-row { display: flex; flex-wrap: wrap; align-items: flex-start; gap: var(--jz-gap-m); min-width: 0; }
.jz-field-control { flex: 1 1 16rem; min-width: 0; }
.jz-field-actions { display: flex; flex-wrap: wrap; align-items: flex-start; gap: var(--jz-gap-s); max-width: 100%; }
.jz-field-description { margin: 0; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: var(--jz-line-height); overflow-wrap: anywhere; }
.jz-field-description.is-error { color: var(--jz-danger); }
@media (max-width: 700px), (pointer: coarse) {
  .jz-field-control { flex-basis: 100%; }
}
</style>
