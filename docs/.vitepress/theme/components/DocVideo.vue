<script setup>
defineProps({
  src: { type: String, required: true }, poster: { type: String, required: true },
  captions: { type: String, required: true }, title: { type: String, required: true },
})
</script>
<template>
  <figure class="doc-video">
    <video controls playsinline preload="none" :poster="poster" :aria-label="title">
      <source :src="src" type="video/mp4" />
      <track kind="captions" :src="captions" srclang="zh-CN" label="中文字幕" default />
      <p>浏览器无法播放此视频。请阅读下方文字步骤，或<a :href="src">下载演示视频</a>。</p>
    </video>
    <figcaption>{{ title }}</figcaption>
    <div class="doc-video-transcript" aria-label="视频文字步骤"><slot /></div>
  </figure>
</template>
