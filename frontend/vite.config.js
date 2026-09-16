import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // jassub 依赖内含 new Worker(new URL(...)) 模式，iife 格式无法代码分割（构建报错）；
  // 实际运行时我们显式传 workerUrl（见 src/jassubLoader.js），这里只为构建通过
  worker: { format: 'es' },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/posters': 'http://127.0.0.1:8080'
    }
  }
})
