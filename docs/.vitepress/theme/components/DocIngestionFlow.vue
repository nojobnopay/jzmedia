<template>
  <ol class="doc-ingestion-flow" aria-label="扫描或上传内容的入库顺序" role="list">
    <li class="doc-ingestion-step">
      <span class="doc-ingestion-number" aria-hidden="true">1</span>
      <div class="doc-ingestion-content">
        <p class="doc-ingestion-title">确认视频库</p>
        <p class="doc-ingestion-copy">检查连接、目录与内容类型。</p>
      </div>
    </li>
    <li class="doc-ingestion-step">
      <span class="doc-ingestion-number" aria-hidden="true">2</span>
      <div class="doc-ingestion-content">
        <p class="doc-ingestion-title">选择添加方式</p>
        <dl class="doc-ingestion-choices">
          <div>
            <dt>文件已在服务器或 NAS</dt>
            <dd>扫描目标视频库</dd>
          </div>
          <div>
            <dt>文件在当前电脑或手机</dt>
            <dd>上传到目标视频库</dd>
          </div>
        </dl>
      </div>
    </li>
    <li class="doc-ingestion-step">
      <span class="doc-ingestion-number" aria-hidden="true">3</span>
      <div class="doc-ingestion-content">
        <p class="doc-ingestion-title">核对并播放</p>
        <p class="doc-ingestion-copy">两种方式都先核对登记结果，再从详情试播。</p>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.doc-ingestion-flow { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 28px; margin: 30px 0; padding: 0; list-style: none; color: var(--vp-c-text-1); }
.doc-ingestion-step { position: relative; display: grid; grid-template-columns: 20px minmax(0, 1fr); column-gap: 10px; align-items: baseline; margin: 0; }
.doc-ingestion-number { color: var(--vp-c-brand-1); font-size: 14px; font-weight: 600; line-height: 1.8; font-variant-numeric: tabular-nums; }
.doc-ingestion-content { min-width: 0; }
.doc-ingestion-title { margin: 0 0 10px; font-size: 18px; font-weight: 600; line-height: 1.6; }
.doc-ingestion-copy { margin: 0; color: var(--vp-c-text-2); font-size: 15px; line-height: 1.8; }
.doc-ingestion-choices { margin: 0; }
.doc-ingestion-choices > div + div { margin-top: 16px; }
.doc-ingestion-choices dt { margin: 0 0 3px; color: var(--vp-c-text-2); font-size: 14px; line-height: 1.8; }
.doc-ingestion-choices dd { margin: 0; font-size: 15px; line-height: 1.8; }
@media (max-width: 640px) {
  .doc-ingestion-flow { grid-template-columns: minmax(0, 1fr); gap: 0; margin: 28px 0; }
  .doc-ingestion-step { padding-bottom: 24px; }
  .doc-ingestion-step:last-child { padding-bottom: 0; }
  .doc-ingestion-step:not(:last-child)::after { position: absolute; top: 32px; bottom: 7px; left: 4px; width: 1px; background: var(--vp-c-divider); content: ''; }
  .doc-ingestion-title { margin-bottom: 6px; }
}
</style>
