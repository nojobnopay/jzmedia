<template>
  <JzDialog ref="dialog" class="budget-quota-editor" presentation="popover" size="small"
    :title="'精确设置' + label" :style="position" :close-on-backdrop="false"
    close-label="取消额度编辑" @close="emit('cancel')">
    <form class="budget-quota-form" @submit.prevent.stop="confirm">
      <JzField :id="inputId" :label="label + '（GB）'" :error="error"
        hint="输入 0.1～102400 GB，最多一位小数。确定后加入本页草稿，保存容量设置后生效。"
        v-slot="{ id, describedby, invalid }">
        <input :id="id" ref="input" type="text" inputmode="decimal" enterkeyhint="done" autocomplete="off"
          :value="value" :disabled="disabled" :aria-describedby="describedby" :aria-invalid="invalid || undefined"
          @input="emit('update:value', $event.target.value)" />
      </JzField>
    </form>
    <template #footer>
      <JzButton type="button" @click="emit('cancel')">取消</JzButton>
      <JzButton type="button" variant="primary" :disabled="disabled" @click="confirm">确定</JzButton>
    </template>
  </JzDialog>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import JzButton from './JzButton.vue'
import JzDialog from './JzDialog.vue'
import JzField from './JzField.vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: String, required: true },
  error: { type: String, default: '' },
  anchor: { type: Object, default: null },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:value', 'confirm', 'cancel'])
const inputId = 'budget-quota-value-' + useId()
const dialog = ref(null), input = ref(null)
const position = ref({ position: 'absolute', left: '12px', right: 'auto', top: '12px', bottom: 'auto',
  width: 'min(360px, calc(100vw - 24px))', maxHeight: 'calc(100dvh - 24px)' })
let mounted = false, viewport = null

function confirm() { if (!props.disabled) emit('confirm') }
async function focus() {
  await nextTick()
  if (!mounted || props.disabled) return
  input.value?.focus({ preventScroll: true })
  input.value?.select()
}
defineExpose({ focus })

function updatePosition() {
  if (!mounted) return
  const element = dialog.value?.element
  const mask = element?.parentElement?.getBoundingClientRect()
  const visual = viewport || window.visualViewport
  const margin = 12, gap = 8
  const visible = {
    left: (visual?.offsetLeft || 0) - (mask?.left || 0),
    top: (visual?.offsetTop || 0) - (mask?.top || 0),
    width: visual?.width || window.innerWidth,
    height: visual?.height || window.innerHeight,
  }
  const width = Math.max(0, Math.min(360, visible.width - 2 * margin))
  const availableHeight = Math.max(0, visible.height - 2 * margin)
  const header = element?.querySelector('.jz-dialog-header')
  const body = element?.querySelector('.jz-dialog-body')
  const footer = element?.querySelector('.jz-dialog-footer')
  const naturalHeight = (header?.offsetHeight || 0) + (body?.scrollHeight || 0)
    + (footer?.offsetHeight || 0) + 2
  const height = Math.min(naturalHeight || 260, availableHeight)
  const mobile = window.innerWidth <= 700
  let left = visible.left + (visible.width - width) / 2
  let top = visible.top + (visible.height - height) / 2
  const anchor = props.anchor?.isConnected ? props.anchor.getBoundingClientRect() : null
  if (!mobile && anchor) {
    left = anchor.left - (mask?.left || 0)
    const below = anchor.bottom - (mask?.top || 0) + gap
    const above = anchor.top - (mask?.top || 0) - gap - height
    top = below + height <= visible.top + visible.height - margin ? below : above
  }
  left = Math.max(visible.left + margin, Math.min(left, visible.left + visible.width - width - margin))
  top = Math.max(visible.top + margin, Math.min(top, visible.top + visible.height - height - margin))
  const bottom = Math.max(0, (mask?.height || window.innerHeight) - visible.top - visible.height) + margin
  position.value = {
    position: 'absolute', left: left + 'px', right: 'auto', width: width + 'px',
    top: mobile ? 'auto' : top + 'px', bottom: mobile ? bottom + 'px' : 'auto',
    maxHeight: (mobile ? availableHeight : visible.top + visible.height - top - margin) + 'px',
  }
}

watch([() => props.anchor, () => props.error, () => props.label], async () => {
  await nextTick()
  updatePosition()
})
onMounted(async () => {
  mounted = true
  viewport = window.visualViewport
  window.addEventListener('resize', updatePosition)
  window.addEventListener('scroll', updatePosition, true)
  viewport?.addEventListener('resize', updatePosition)
  viewport?.addEventListener('scroll', updatePosition)
  updatePosition()
  await nextTick()
  updatePosition()
  await focus()
})
onBeforeUnmount(() => {
  mounted = false
  window.removeEventListener('resize', updatePosition)
  window.removeEventListener('scroll', updatePosition, true)
  viewport?.removeEventListener('resize', updatePosition)
  viewport?.removeEventListener('scroll', updatePosition)
})
</script>

<style>
.budget-quota-editor .jz-dialog-header { flex: none; padding: 16px 20px; }
.budget-quota-editor .jz-dialog-heading h2 { font-size: var(--jz-font-l); line-height: 1.5; }
.budget-quota-editor .jz-dialog-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 16px 20px; }
.budget-quota-editor .jz-dialog-footer { flex: none; padding: 12px 20px; }
.budget-quota-editor .budget-quota-form { margin: 0; }
.budget-quota-editor .jz-field { margin: 0; }
.budget-quota-editor input { box-sizing: border-box; width: 100%; min-height: var(--jz-touch-target); font-size: var(--jz-font-body); }
@media (max-width: 700px) {
  .budget-quota-editor .jz-dialog-header, .budget-quota-editor .jz-dialog-body { padding: 14px 16px; }
  .budget-quota-editor .jz-dialog-footer { padding: 12px 16px; }
}
</style>
