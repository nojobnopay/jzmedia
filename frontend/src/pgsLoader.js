// libpgs 懒加载：仅选中 PGS 图片字幕时才下载（自包含 worker，含解码器）。
// workerUrl 必须显式传：libpgs 默认值 'libpgs.worker.js' 是相对路径，打包后必 404。
let mod = null

export async function ensurePgs () {
  if (!mod) {
    const [pkg, workerUrl] = await Promise.all([
      import('libpgs'),
      import('libpgs/dist/libpgs.worker.js?worker&url').then((m) => m.default),
    ])
    mod = { PgsRenderer: pkg.PgsRenderer, workerUrl }
  }
  return mod
}
