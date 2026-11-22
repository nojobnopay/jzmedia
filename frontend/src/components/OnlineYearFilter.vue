<template>
  <div class="online-year-controls">
    <JzField id="online-year" label="年份"><template #default="field"><select :id="field.id" :value="yearChoice" :disabled="disabled" data-online-year @change="$emit('choose', $event.target.value)"><option value="">全部年份</option><option v-for="value in recentYears" :key="value" :value="value">{{ value }} 年</option><option value="custom">其他年份…</option></select></template></JzField>
    <form v-if="yearChoice === 'custom'" class="online-custom-year" data-online-year-form @submit.prevent="$emit('apply')">
      <JzField id="online-year-custom" label="指定年份" :error="customYearError"><template #default="field"><input :id="field.id" v-model="customYear" inputmode="numeric" maxlength="4" autocomplete="off" placeholder="如 2018" :disabled="disabled" :aria-invalid="field.invalid || undefined" :aria-describedby="field.describedby" data-online-year-custom @input="$emit('edit')" /></template></JzField>
      <JzButton type="submit" :disabled="disabled">应用年份</JzButton>
    </form>
  </div>
</template>

<script setup>
import JzField from './JzField.vue'
import JzButton from './JzButton.vue'

const customYear = defineModel('customYear', { type: String, required: true })
defineProps({ yearChoice: String, recentYears: Array, customYearError: String, disabled: Boolean })
defineEmits(['choose', 'apply', 'edit'])
</script>
