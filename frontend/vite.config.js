import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // VITE_API_TARGET：dev 代理的后端地址，默认 8080；本机可在 .env.local 覆盖。
  const target = loadEnv(mode, process.cwd(), '').VITE_API_TARGET || 'http://127.0.0.1:8080'
  return {
    plugins: [vue()],
    // jassub 依赖内含 new Worker(new URL(...)) 模式，iife 格式无法代码分割（构建报错）；
    // 实际运行时我们显式传 workerUrl（见 src/jassubLoader.js），这里只为构建通过
    worker: { format: 'es' },
    server: {
      port: 5173,
      proxy: {
        '/api': target,
        '/help': target,
        '/posters': target
      }
    }
  }
})
