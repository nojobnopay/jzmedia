<template>
  <nav>
    <router-link to="/" class="brand"><img src="/favicon.svg" alt="jzmedia" width="26" height="26" /><span>jzmedia</span></router-link>
    <router-link to="/">库</router-link>
    <router-link to="/collections">合集</router-link>
    <router-link to="/settings">设置</router-link>
  </nav>
  <router-view />
</template>
<style>
body { font-family: system-ui, sans-serif; margin: 0; background: #141414; color: #eee; }
nav { padding: 12px; background: #1f1f1f; display: flex; gap: 16px; align-items: center; }
nav a { color: #eee; text-decoration: none; }
nav a.router-link-active { color: #e50914; font-weight: bold; }
nav .brand { display: inline-flex; align-items: center; gap: 8px; font-weight: 800; letter-spacing: 0.5px; }
nav .brand img { border-radius: 6px; display: block; }
nav .brand.router-link-active { color: #eee; font-weight: 800; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--poster-min, 150px), 1fr)); gap: 12px; padding: 12px; }
.card { background: #222; border-radius: 8px; overflow: hidden; cursor: pointer; }
.card img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.card .t { padding: 8px; font-size: 0.875rem; }
.bar { padding: 12px; display: flex; gap: 8px; }
input, button, textarea { font-size: 0.875rem; padding: 6px 10px; border-radius: 6px; border: 1px solid #444; background: #222; color: #eee; }
input[type="range"] { padding: 0; }
button { cursor: pointer; }
.page { padding: 12px; max-width: 900px; }
.actor { cursor: pointer; color: #6ab0ff; }
.poster-wrap { position: relative; }
.poster-wrap img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.card img { width: 100%; aspect-ratio: 2/3; object-fit: cover; display: block; }
.score-badge { position: absolute; top: 6px; right: 6px; font-size: 0.75rem; font-weight: bold; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.72); color: #ffc107; white-space: nowrap; }
.score-badge.douban { color: #7ed321; }
.score-badge.custom { color: #ff6b6b; }
.stars { color: #ffc107; letter-spacing: 1px; }
.rate-chip { display: inline-block; font-size: 0.9375rem; padding: 4px 14px; border-radius: 999px; border: 1px solid #444; margin-right: 6px; }
.rate-chip.tmdb { color: #ffc107; border-color: #6b5518; }
.rate-chip.douban { color: #7ed321; border-color: #3a5a1e; }
.rate-chip.custom { color: #ff6b6b; border-color: #6e2b2b; }
.rating-row { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
</style>
