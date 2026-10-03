<template>
  <main class="preview-shell">
    <header class="preview-heading">
      <p class="preview-eyebrow">JZMEDIA / 组件预览</p>
      <h1>让每一次操作都有一致的体验</h1>
      <p>实际组件、统一变量。此页面使用独立演示状态，不连接媒体库。</p>
    </header>

    <section aria-labelledby="buttons-title" class="preview-section">
      <div class="preview-section-heading"><span>01</span><h2 id="buttons-title">按钮与图标</h2></div>
      <p class="preview-description">主要动作使用红色，同级操作保持轻量；危险操作保留明确的文字说明。</p>
      <div class="preview-controls" data-touch-check>
        <JzButton id="primary-sample" variant="primary">保存设置</JzButton>
        <JzButton>取消</JzButton>
        <JzButton variant="danger">移除媒体库</JzButton>
        <JzButton variant="ghost"><AppIcon name="back" />返回</JzButton>
        <JzButton disabled>暂不可用</JzButton>
        <JzButton loading variant="primary">正在保存</JzButton>
      </div>
      <div class="preview-controls" data-touch-check>
        <JzButton size="compact"><AppIcon name="refresh" />刷新</JzButton>
        <JzButton size="compact" variant="primary">紧凑主按钮</JzButton>
        <JzButton size="compact" variant="ghost" aria-label="更多操作"><AppIcon name="more" /></JzButton>
      </div>

    </section>

    <section aria-labelledby="forms-title" class="preview-section">
      <div class="preview-section-heading"><span>02</span><h2 id="forms-title">表单与提示</h2></div>
      <form class="preview-form" @submit.prevent="saved = true">
        <JzField id="library-name" label="视频库名称" hint="名称会显示在媒体库切换菜单中。" v-slot="field">
          <input :id="field.id" v-model="libraryName" :aria-describedby="field.describedby" :aria-invalid="field.invalid" autocomplete="off" />
        </JzField>
        <JzField id="library-kind" label="内容类型" v-slot="field">
          <select :id="field.id"><option>电影</option><option>剧集</option></select>
        </JzField>
        <JzField id="invalid-example" label="目录路径（错误示例）" error="请输入目录路径。" v-slot="field">
          <input :id="field.id" :aria-describedby="field.describedby" :aria-invalid="field.invalid" />
        </JzField>
        <label class="preview-check"><input type="checkbox" checked />启用这个视频库</label>
        <div class="preview-controls"><JzButton type="submit" variant="primary">保存演示表单</JzButton></div>
        <p v-if="saved" role="status" class="preview-success"><AppIcon name="check" />演示设置已保存，仅在当前页面生效。</p>
      </form>
      <p class="preview-notice"><AppIcon name="warning" />这是独立组件预览，操作不会修改真实媒体或配置。</p>
    </section>

    <section aria-labelledby="states-title" class="preview-section">
      <div class="preview-section-heading"><span>03</span><h2 id="states-title">页面状态</h2></div>
      <div class="preview-states">
        <div><h3>加载中</h3><EmptyState state="loading" text="正在加载媒体…" /></div>
        <div><h3>暂无内容</h3><EmptyState state="empty" title="还没有影片" text="添加视频库后即可开始扫描。"><JzButton>添加视频库</JzButton></EmptyState></div>
        <div><h3>搜索无结果</h3><EmptyState state="no-results" title="没有找到符合条件的影片" text="可以尝试减少筛选条件。"><JzButton variant="ghost">清除筛选</JzButton></EmptyState></div>
        <div><h3>请求失败</h3><EmptyState state="error" title="暂时无法加载" text="请检查连接后重试。" retry @retry="retryCount++" /><p v-if="retryCount" role="status">已尝试 {{ retryCount }} 次演示重试</p></div>
      </div>
    </section>

    <section aria-labelledby="dialogs-title" class="preview-section">
      <div class="preview-section-heading"><span>04</span><h2 id="dialogs-title">弹窗</h2></div>
      <p class="preview-description">统一标题与操作区。支持键盘焦点循环、Esc 关闭和关闭后焦点恢复；任务进行时保留窗口。</p>
      <div class="preview-controls" data-touch-check>
        <JzButton id="dialog-trigger" @click="openDialog('medium')">打开标准弹窗</JzButton>
        <JzButton @click="openDialog('small')">小尺寸弹窗</JzButton>
        <JzButton @click="openDialog('large')">大尺寸弹窗</JzButton>
        <JzButton id="busy-trigger" @click="openDialog('medium', true)">查看执行中状态</JzButton>
      </div>
      <p class="preview-description">键盘检查：打开弹窗后按 Tab / Shift+Tab，再按 Esc 返回触发按钮。</p>
    </section>
    <DesignAssetCatalogue />
  </main>

  <JzDialog v-if="dialogOpen" :size="dialogSize" :busy="busy" title="确认演示设置" @close="closeDialog">
    <p class="preview-dialog-copy">此弹窗直接渲染共享组件，并挂载到页面 body。按钮和表单不依赖设置页面的样式。</p>
    <label class="preview-dialog-label" for="dialog-note">备注<input id="dialog-note" placeholder="可选" /></label>
    <p v-if="busy" role="status" class="preview-notice">演示任务正在进行，此时不能关闭窗口。</p>
    <JzButton v-if="busy" id="finish-busy" @click="busy = false">结束演示任务</JzButton>
    <JzButton v-else id="nested-trigger" @click="nestedOpen = true">选择演示目录</JzButton>
    <template #footer>
      <JzButton :disabled="busy" @click="closeDialog">取消</JzButton>
      <JzButton id="dialog-primary" variant="primary" :loading="busy" @click="closeDialog">确认保存</JzButton>
    </template>
  </JzDialog>
  <JzDialog v-if="nestedOpen" title="选择演示目录" size="small" @close="nestedOpen = false">
    <p class="preview-dialog-copy">子窗口关闭后，键盘焦点返回上一个窗口。</p>
    <template #footer><JzButton variant="primary" @click="nestedOpen = false">使用这个目录</JzButton></template>
  </JzDialog>
</template>

<script setup>
import { ref } from 'vue'
import JzButton from '../../frontend/src/components/JzButton.vue'
import JzDialog from '../../frontend/src/components/JzDialog.vue'
import JzField from '../../frontend/src/components/JzField.vue'
import AppIcon from '../../frontend/src/components/AppIcon.vue'
import EmptyState from '../../frontend/src/components/EmptyState.vue'
import DesignAssetCatalogue from './DesignAssetCatalogue.vue'

const libraryName = ref('家庭电影'), saved = ref(false), retryCount = ref(0)
const dialogOpen = ref(false), dialogSize = ref('medium'), busy = ref(false), nestedOpen = ref(false)
function openDialog(size, working = false) {
  dialogSize.value = size
  busy.value = working
  dialogOpen.value = true
}
function closeDialog() { if (!busy.value) dialogOpen.value = false }
</script>

<style scoped>
.preview-shell { max-width: 1080px; margin: 0 auto; padding: 48px 24px 72px; }
.preview-heading { padding-bottom: 24px; }
.preview-eyebrow { color: var(--jz-accent); font-size: var(--jz-font-s); font-weight: 700; letter-spacing: .12em; }
.preview-heading h1 { font-size: clamp(1.65rem, 4vw, 2.5rem); line-height: 1.35; margin: 16px 0; }
.preview-heading > p:last-child, .preview-description { color: var(--jz-text-dim); line-height: 1.7; }
.preview-section { background: var(--jz-surface); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); padding: 24px; margin-top: 20px; }
.preview-section-heading { display: flex; align-items: center; gap: 12px; }
.preview-section-heading > span { color: var(--jz-text-faint); font-size: var(--jz-font-s); font-variant-numeric: tabular-nums; }
.preview-section-heading h2 { font-size: 1.25rem; margin: 0; }
.preview-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.preview-icons { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 12px; border-top: 1px solid var(--jz-border); padding-top: 20px; margin-top: 24px; }
.preview-icons > div { display: flex; flex-direction: column; gap: 8px; align-items: center; color: var(--jz-text-dim); }
.preview-icons code { font-size: var(--jz-font-s); }
.preview-form { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; }
.preview-form > .jz-field { margin: 0; }
.preview-form > label:not(.preview-check), .preview-dialog-label { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.preview-form input:not([type="checkbox"]), .preview-form select, .preview-dialog-label input { box-sizing: border-box; width: 100%; min-width: 0; }
.preview-form > p, .preview-form > .preview-check, .preview-form > .preview-controls { grid-column: 1 / -1; margin: 0; }
.preview-check { display: flex; align-items: center; gap: 8px; min-height: var(--jz-touch-target); }
.preview-notice, .preview-success { display: flex; align-items: flex-start; gap: 8px; padding: 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); line-height: 1.6; }
.preview-notice { color: var(--jz-warn); }
.preview-success { color: var(--jz-green); }
.preview-notice > svg, .preview-success > svg { flex: 0 0 auto; margin-top: 3px; }
.preview-states { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; }
.preview-states > div { border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); min-width: 0; padding: 16px; }
.preview-states h3 { color: var(--jz-text-dim); font-size: var(--jz-font-m); margin: 0 0 8px; }
.preview-dialog-copy { color: var(--jz-text-dim); line-height: 1.7; }
@media (max-width: 700px) {
  .preview-shell { padding: 28px 12px 48px; }
  .preview-section { padding: 20px 16px; }
  .preview-form, .preview-states { grid-template-columns: 1fr; }
}
</style>
