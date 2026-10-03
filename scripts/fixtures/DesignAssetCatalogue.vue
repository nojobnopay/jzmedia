<template>
  <section class="asset-catalogue" aria-labelledby="asset-catalogue-title">
    <h2 id="asset-catalogue-title">图标与用途图鉴</h2>
    <p>一份母版用于 Web、手机和电视。显示尺寸、点击区域和导出分辨率分别登记。</p>
    <div class="catalogue-filters">
      <label>查找资产与用途<input v-model="query" type="search" placeholder="名称、功能、组件或语义 ID" /></label>
      <label>平台<select v-model="platform"><option value="">全部平台</option><option value="web">Web / 手机</option><option value="android-tv">Android TV</option></select></label>
    </div>
    <p role="status">{{ visibleAssets.length }} 项资产 · {{ matchingRequirements.length }} 项使用需求</p>
    <div class="asset-grid" aria-label="全部资产">
      <article v-for="asset in visibleAssets" :key="asset.id" :data-asset-id="asset.id">
        <div v-if="isIcon(asset.id)" class="size-preview">
          <span v-for="size in [16, 20, 24, 32]" :key="size"><AppIcon :name="asset.id" :size="size" /><small>{{ size }}px</small></span>
        </div>
        <img v-else :src="brandImages[asset.id]" :alt="asset.name" class="brand-preview" />
        <h3>{{ asset.name }}</h3><code>{{ asset.id }}</code>
        <p class="asset-meta">{{ asset.grid.join(' × ') }} · {{ asset.style || asset.category }} · 修订 {{ asset.revision }}</p>
        <details><summary>来源与使用位置</summary>
          <p>{{ asset.source }}</p><p>母版：{{ asset.master }}</p>
          <p>主题：{{ asset.theme }}</p>
          <ul><li v-for="usage in uses(asset.id)" :key="usage.id">{{ usage.feature }} · {{ usage.purpose }}<br /><code>{{ usage.source }}:{{ usage.line }}</code><br />{{ usage.display_size.value }} {{ usage.display_size.unit }}</li></ul>
          <p v-if="!uses(asset.id).length">已登记的基础资产；当前没有直接调用。</p>
        </details>
      </article>
    </div>
    <h3>按钮状态与平台表现</h3>
    <div class="state-preview" data-touch-check>
      <JzButton icon="play" variant="primary">播放</JzButton>
      <JzButton icon="copy">复制</JzButton>
      <JzButton icon="check" aria-pressed="true">已选中</JzButton>
      <JzButton icon="delete" variant="danger">删除</JzButton>
      <JzButton icon="refresh" disabled>刷新不可用</JzButton>
      <JzButton icon="upload" loading>正在上传</JzButton>
      <JzButton icon="settings" icon-only variant="ghost" aria-label="图鉴设置" />
    </div>
    <p>将指针悬停、按住按钮或按 Tab 检查实际状态。电视预览中的白底表示焦点，红色短线表示当前选择。</p>
    <div class="tv-preview"><span><AppIcon name="home" :size="24" />首页<i /></span><span><AppIcon name="movie" :size="24" />电影</span></div>
    <h3>需求登记</h3>
    <p>显示尺寸为 CSS px 或 dp；手机触摸目标至少 44px，电视按设备密度渲染。1080p / 4K 的真机证据另行记录。</p>
    <details class="requirements"><summary>浏览 {{ matchingRequirements.length }} 条使用需求</summary>
      <ol><li v-for="usage in matchingRequirements.slice(0, shown)" :key="usage.id">
        <strong>{{ usage.feature }} · {{ usage.purpose }}</strong>
        <p>{{ usage.element }} · {{ usage.platforms.join(' / ') }} · {{ usage.classification }}</p>
        <code>{{ usage.source }}:{{ usage.line }}</code>
        <p>资产：{{ usage.assets.join(', ') || '文字 / 原生控件' }}；显示：{{ usage.display_size.value }} {{ usage.display_size.unit }}</p>
        <p>状态：{{ usage.states.join(' / ') }}；可访问名称：{{ usage.accessible_name_owner }}</p>
      </li></ol>
      <JzButton v-if="shown < matchingRequirements.length" @click="shown += 40">显示更多需求</JzButton>
    </details>
    <details><summary>品牌导出规格</summary><ul><li v-for="item in catalog.exports" :key="item.path"><code>{{ item.path }}</code> — {{ item.format }} {{ item.dimensions?.join(' × ') }} {{ item.units }} · {{ item.density }}</li></ul></details>
  </section>
</template>
<script setup>
import { computed, ref } from 'vue'
import AppIcon from '../../frontend/src/components/AppIcon.vue'
import JzButton from '../../frontend/src/components/JzButton.vue'
import { iconRegistry } from '../../frontend/src/generated/design-icons.js'
import catalog from '../../design/catalog.json'
import mark from '../../design/brand/mark.svg'
import wordmark from '../../design/brand/wordmark.svg'
import favicon16 from '../../design/brand/favicon-16.svg'
import favicon32 from '../../design/brand/favicon-32.svg'
const brandImages = { 'brand-mark': mark, 'brand-wordmark': wordmark, 'brand-favicon-16': favicon16, 'brand-favicon-32': favicon32 }
const query = ref(''), platform = ref(''), shown = ref(40)
const isIcon = name => Object.hasOwn(iconRegistry, name)
const uses = id => catalog.requirements.filter(usage => usage.assets.includes(id) && (!platform.value || usage.platforms.includes(platform.value)))
const matchesQuery = value => JSON.stringify(value).toLowerCase().includes(query.value.trim().toLowerCase())
const matchingRequirements = computed(() => catalog.requirements.filter(usage => (!platform.value || usage.platforms.includes(platform.value)) && matchesQuery(usage)))
const visibleAssets = computed(() => catalog.assets.filter(asset => (!platform.value || uses(asset.id).length) && (matchesQuery(asset) || uses(asset.id).some(matchesQuery))))
</script>
<style scoped>
.asset-catalogue { padding: 24px; margin-top: 24px; background: var(--jz-surface); border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); }
.asset-catalogue p { color: var(--jz-text-dim); line-height: 1.6; }
.catalogue-filters { display: flex; flex-wrap: wrap; gap: 16px; }
.catalogue-filters label { display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: min(220px, 100%); }
.catalogue-filters input, .catalogue-filters select { width: 100%; }
.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.asset-grid article { min-width: 0; padding: 16px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-m); }
.asset-grid h3 { margin: 12px 0 6px; font-size: var(--jz-font-m); }
.asset-catalogue code, .asset-catalogue li, .asset-catalogue details p { overflow-wrap: anywhere; font-size: var(--jz-font-s); }
.asset-meta { font-size: var(--jz-font-s); }
.size-preview { display: flex; gap: 16px; min-height: 52px; align-items: end; }
.size-preview span { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.size-preview small { color: var(--jz-text-faint); }
.brand-preview { max-width: 100%; height: 52px; object-fit: contain; }
.asset-catalogue summary { color: var(--jz-link); padding: 8px 0; }
.asset-catalogue ul, .asset-catalogue ol { padding-left: 18px; }
.requirements li { padding-bottom: 16px; }
.state-preview { display: flex; flex-wrap: wrap; gap: 12px; }
.tv-preview { display: flex; gap: 16px; flex-wrap: wrap; }
.tv-preview span { position: relative; display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-radius: var(--jz-radius-l); }
.tv-preview span:first-child { background: var(--jz-on-accent); color: var(--jz-bg); }
.tv-preview i { position: absolute; width: 20px; height: 3px; background: var(--jz-accent); bottom: 4px; left: calc(50% - 10px); }
@media (max-width: 700px) { .asset-catalogue { padding: 20px 16px; } }
</style>
