// JASSUB 懒加载：仅选中 ASS/SSA 字幕时才下载（worker + wasm 约 2MB）。
// 显式传 URL：本地入口先禁用字幕画布的 desynchronized 提示，再加载原 RPC worker
//（dist/worker/worker.js，内部加载 emscripten glue 与 wasm），保持 ASS 样式与 WebGL。
let mod = null

export async function ensureJassub () {
  if (!mod) {
    const [pkg, workerUrl, wasmUrl, modernWasmUrl] = await Promise.all([
      import('jassub'),
      import('./jassubWorker.js?worker&url').then((m) => m.default),
      import('jassub/dist/wasm/jassub-worker.wasm?url').then((m) => m.default),
      import('jassub/dist/wasm/jassub-worker-modern.wasm?url').then((m) => m.default),
    ])
    mod = { JASSUB: pkg.default, workerUrl, wasmUrl, modernWasmUrl }
  }
  return mod
}
