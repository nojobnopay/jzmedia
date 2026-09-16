// JASSUB 懒加载：仅选中 ASS/SSA 字幕时才下载（worker + wasm 约 2MB）。
// 显式传 URL：workerUrl 必须是 RPC worker（dist/worker/worker.js，内部再加载 emscripten
// glue 与 wasm）；依赖包内的 new URL(..., import.meta.url) 在打包后不可靠。
let mod = null

export async function ensureJassub () {
  if (!mod) {
    const [pkg, workerUrl, wasmUrl, modernWasmUrl] = await Promise.all([
      import('jassub'),
      import('jassub/dist/worker/worker.js?worker&url').then((m) => m.default),
      import('jassub/dist/wasm/jassub-worker.wasm?url').then((m) => m.default),
      import('jassub/dist/wasm/jassub-worker-modern.wasm?url').then((m) => m.default),
    ])
    mod = { JASSUB: pkg.default, workerUrl, wasmUrl, modernWasmUrl }
  }
  return mod
}
