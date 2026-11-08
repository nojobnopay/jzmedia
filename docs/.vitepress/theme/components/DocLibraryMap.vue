<template>
  <figure class="doc-library-map" aria-label="媒体库与视频库的目录层级示例">
    <div class="doc-library-root">
      <p class="doc-library-label">媒体库</p>
      <code>/app/media</code>
      <p class="doc-library-description">一个存储连接或根目录</p>
    </div>
    <ul class="doc-library-children">
      <li>
        <p class="doc-library-name">电影视频库</p>
        <code>Movies/</code>
      </li>
      <li>
        <p class="doc-library-name">剧集视频库</p>
        <code>TV Shows/</code>
      </li>
    </ul>
  </figure>
</template>

<style scoped>
.doc-library-map { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 40px; align-items: center; margin: 28px 0; color: var(--vp-c-text-1); }
.doc-library-root { position: relative; min-width: 0; }
.doc-library-root::after { position: absolute; top: 50%; right: -40px; width: 32px; border-top: 1px solid var(--vp-c-divider); content: ''; }
.doc-library-label { margin: 0 0 6px; color: var(--vp-c-text-2); font-size: 14px; line-height: 1.6; }
.doc-library-description { margin: 8px 0 0; color: var(--vp-c-text-2); font-size: 14px; line-height: 1.7; }
.doc-library-map code { padding: 0; background: none; color: inherit; font-size: 16px; line-height: 1.8; overflow-wrap: anywhere; }
.doc-library-root > code { font-size: 20px; }
.doc-library-children { margin: 0; padding: 0; border-left: 1px solid var(--vp-c-divider); list-style: none; }
.doc-library-children > li { position: relative; margin: 0; padding: 8px 0 8px 24px; }
.doc-library-children > li + li { margin-top: 14px; }
.doc-library-children > li::before { position: absolute; top: 24px; left: 0; width: 14px; border-top: 1px solid var(--vp-c-divider); content: ''; }
.doc-library-name { margin: 0 0 2px; font-size: 16px; line-height: 1.8; font-weight: 600; }
@media (max-width: 640px) {
  .doc-library-map { grid-template-columns: minmax(0, 1fr); gap: 18px; }
  .doc-library-root::after { display: none; }
  .doc-library-children { margin-left: 12px; }
}
</style>
