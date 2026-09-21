// 字幕子系统 composable（自 PlayerModal.vue 抽离，评审 R13-Q1/R14-Q1）：
// 选轨/默认轨、本地临时字幕、VTT 自绘层、ASS(JASSUB)、PGS(libpgs)、延迟/外观，
// 以及按所选轨的渲染分发。对外 API 与原组件内标识符同名，PlayerModal 解构后调用面不变。
//
// ctx 契约：
//   videoEl/videoKey: ref；versionId/kindParam/isEpisode/getMethod/getMediaStart/getBurnOn: getter
//   reload/toast/logEvt: 回调（reload 为 PlayerModal 的会话重载）
import { computed, ref } from 'vue'

import { api } from './api.js'
import { ensureJassub } from './jassubLoader.js'
import { ensurePgs } from './pgsLoader.js'
import { subKind } from './playerLabels.js'
import { parseVtt, activeCues, pickDefaultSub, decodeSubtitleBytes, localSubCodec } from './subtitleParse.js'
import { normalizeSubStyle, subFontPx, pickSubAnchor, subBarPad, subInnerPad } from './subStyle.js'

export function useSubtitles(ctx) {
  const versionId = () => ctx.versionId()
  const kindParam = () => ctx.kindParam()
  const videoEl = ctx.videoEl
  const videoKey = ctx.videoKey

  // ================= 选轨/本地字幕状态 =================
  const subIdx = ref(-1)
  const subs = ref([])
  // 临时加载的本地字幕（2026-09 用户需求）：浏览器端解析/渲染，不入库，关播放器即失效；
  // 影片重载（切档/seek/音轨）后合并保留，选中项按 localId 恢复。
  const localSubs = ref([])
  let localSubSeq = 0
  // 外挂中文默认轨只自动选一次（用户手动选过后不再自动覆盖）
  let autoSubPicked = false
  // 缺字体时自动转 VTT 的字幕轨索引（按轨，不污染其他 ASS 轨；重开播放器即重置）
  const autoVttSub = ref(-1)
  // 「兼容字幕(VTT)」：ASS 样式渲染异常/无字体时的降级；跨会话记住选择
  const compatSub = ref((() => {
    try { return localStorage.getItem('jzmedia.subCompat') === '1' } catch (e) { return false } })
  )()

  function selectedSub() {
    return subs.value[subIdx.value] || null
  }
  function isLocalSub(s) {
    return !!(s && s.source === 'local')
  }
  // 所选字幕是否为图片型（需烧录；实际是否烧录以服务端 subtitle_mode 为准）
  function imageSubSelected() {
    const s = selectedSub()
    return !!(s && s.image)
  }
  // 服务端轨清单与本地临时字幕合并（每次 reload 后调用；本地轨追加在末尾，索引重排；
  // 选中项若为本地轨按 localId 找回，避免清单变化后错位）。
  function mergeSubs(serverSubs) {
    const sel = selectedSub()
    const selLocalId = isLocalSub(sel) ? sel.localId : ''
    subs.value = [...(serverSubs || []), ...localSubs.value]
    subs.value.forEach((s, i) => { s.index = i })
    if (selLocalId) {
      subIdx.value = subs.value.findIndex(s => s.localId === selLocalId)
    } else if (subIdx.value >= subs.value.length) {
      subIdx.value = -1
    }
  }
  // 会话重载入口：合并服务端轨 + 未手选过时自动选默认轨（规则见 pickDefaultSub）
  function syncForSession(serverSubs) {
    mergeSubs(serverSubs)
    if (subIdx.value === -1 && !autoSubPicked) {
      const di = pickDefaultSub(subs.value)
      if (di >= 0) { subIdx.value = di; autoSubPicked = true }
    }
  }
  function revokeLocalUrl(s) {
    if (s && s.blobUrl) {
      try { URL.revokeObjectURL(s.blobUrl) } catch (e) { /* 忽略 */ }
    }
  }
  // 临时加载本地字幕文件（srt/vtt 自绘、ass/ssa JASSUB；图片格式不支持，见 localSubCodec）。
  // 纯浏览器端：不入库、不上传，关播放器即失效；同名文件重复加载自动替换。
  async function onLoadSubFile(file) {
    const codec = localSubCodec(file && file.name)
    if (!codec) {
      ctx.toast('不支持的字幕格式：仅可临时加载 srt/vtt/ass/ssa（图片字幕请与正片同名放入片目录）')
      return
    }
    const localId = 'loc' + (++localSubSeq)
    const entry = { localId, source: 'local', title: (file && file.name) || ('本地字幕 ' + localSubSeq),
                    codec, image: 0, lang: '', default: 0, forced: 0 }
    try {
      if (codec === 'ass' || codec === 'ssa') {
        entry.url = URL.createObjectURL(file)
      } else {
        entry.text = decodeSubtitleBytes(await file.arrayBuffer())
        entry.url = URL.createObjectURL(new Blob([entry.text], { type: 'text/vtt' }))   // 原生 <track> 兜底用
      }
      entry.blobUrl = entry.url
    } catch (e) {
      ctx.toast('字幕文件读取失败：' + ((e && e.message) || e))
      return
    }
    const prev = localSubs.value.findIndex(s => s.title === entry.title)
    if (prev >= 0) {
      revokeLocalUrl(localSubs.value[prev])
      localSubs.value.splice(prev, 1)
    }
    localSubs.value.push(entry)
    mergeSubs(subs.value.filter(s => s.source !== 'local'))
    const li = subs.value.findIndex(s => s.localId === localId)
    if (li < 0) return
    subIdx.value = li
    autoSubPicked = true
    ctx.toast(`已加载临时字幕：${entry.title}（仅本次播放，不入库）`)
    if (ctx.getBurnOn()) { ctx.reload(); return }   // 前一轨是烧录会话：重开会话去掉画面里烧死的字幕
    applySubs(true)
  }
  function removeLocalSubs() {
    if (!localSubs.value.length) return
    const wasLocal = isLocalSub(selectedSub())
    for (const s of localSubs.value) revokeLocalUrl(s)
    localSubs.value = []
    mergeSubs(subs.value.filter(s => s.source !== 'local'))
    if (wasLocal) subIdx.value = -1
    ctx.toast('已移除临时字幕')
    if (ctx.getBurnOn()) { ctx.reload(); return }
    applySubs(true)
  }

  // ================= 字幕延迟 / 外观 =================
  const subDelayKey = computed(() => 'jzmedia.subDelay.' + (ctx.isEpisode() ? 'ep.' : '') + versionId())
  const subDelay = ref(0)
  // 字幕延迟按版本记忆（打开播放器即恢复；ASS/PGS 走 timeOffset，VTT 自绘渲染时叠加）
  try {
    const saved = Number(localStorage.getItem(subDelayKey.value))
    if (Number.isFinite(saved)) subDelay.value = Math.round(saved * 10) / 10
  } catch (e) { /* 忽略 */ }
  const subDelayText = computed(() => (subDelay.value > 0 ? '+' : '') + subDelay.value.toFixed(1) + 's')
  // 字幕外观/定位（全局记忆）：背景 0无/1半透明/2纯黑；描边 0无/1细/2粗；
  // 位置 auto（黑边优先）/inside/outside；字号 1小/2中/3大。纯计算见 ../subStyle.js
  const subStyle = ref(loadSubStyle())
  function loadSubStyle() {
    try {
      return normalizeSubStyle(JSON.parse(localStorage.getItem('jzmedia.subStyle') || '{}'))
    } catch (e) { return normalizeSubStyle(null) }
  }
  function onSubStyleSet(v) { subStyle.value = v; onSubStyleChange() }
  // 「兼容字幕(VTT)」：ASS 样式渲染异常/无字体时的降级开关
  function onCompatSet(v) { compatSub.value = v; onCompatChange() }
  const forceBurn = ref(false)
  // 当前所选是否为 ASS/SSA（决定「兼容」开关是否显示）；本地临时 ASS 无服务端 VTT 变体，不显示
  const subIsAss = computed(() => {
    const s = selectedSub()
    if (isLocalSub(s)) return false
    return subKind(s) === 'ass'
  })
  // 文本(VTT 自绘)/ASS/PGS 都支持 timeOffset（延迟控件可见）
  const subDelayVisible = computed(() => {
    if (ctx.getBurnOn()) return false
    const s = selectedSub()
    const k = subKind(s)
    if (k === 'vtt') return true
    if (isLocalSub(s)) return k === 'ass'   // 本地 ASS 直接 JASSUB 渲染，不受全局兼容开关影响
    return ['ass', 'pgs'].includes(k) && !compatSub.value && autoVttSub.value !== Number(subIdx.value)
  })
  // 撤销自动降级（评审 B8/R13-Q3）：VTT 兼容/烧录/原生兜底一键恢复客户端渲染
  const undoDegradeDisabled = computed(() => !forceBurn.value && !subIsAss.value
    && autoVttSub.value < 0 && !vttNativeFallback.value)
  function undoDegrade() {
    forceBurn.value = false
    autoVttSub.value = -1
    compatSub.value = false
    try { localStorage.setItem('jzmedia.subCompat', '0') } catch (e) { /* 忽略 */ }
    vttNativeFallback.value = false
    ctx.toast('已恢复客户端渲染，正在重新加载…')
    ctx.reload()
  }
  function onCompatChange() {
    try { localStorage.setItem('jzmedia.subCompat', compatSub.value ? '1' : '0') } catch (e) { /* 忽略 */ }
    applySubs(true)
  }
  // 字幕切换：文本/ASS/PGS 都是客户端渲染层 → 即时切换不重开会话；
  // VobSub（burn）或已处于烧录模式（forceBurn 降级）才重开转码
  function onSubChange(v) {
    subIdx.value = v
    autoSubPicked = true   // 用户手动选过字幕，不再自动选外挂默认轨
    if (ctx.getBurnOn() || subKind(subs.value[subIdx.value]) === 'burn') { ctx.reload(); return }
    applySubs(true)
  }

  // ---- 会话时间轴平移：片内 0 对应的源时间（direct=原文件时间轴，不需平移）----
  function subShift() {
    return ctx.getMethod() === 'direct' ? 0 : ctx.getMediaStart()
  }

  // ================= ASS（JASSUB） =================
  let jassub = null
  let assKey = ''
  const assFonts = ref(-1)

  function destroyAss() {
    const inst = jassub
    jassub = null
    assKey = ''
    if (inst) { try { inst.destroy() } catch (e) { /* 忽略 */ } }
  }
  // ASS 是否含中日韩文本（无字体时判断是否需要降级 VTT）。全文判定（评审 R13-D3）：
  // 前 40KB 多为样式段，正文中文会漏判；2MB 上限防极端文件
  async function assNeedsCjk(url) {
    try {
      const r = await fetch(url, { cache: 'no-store' })
      const t = (await r.text()).slice(0, 2000000)
      return /[\u2E80-\u9FFF\uF900-\uFAFF\u3400-\u4DBF\uAC00-\uD7AF]/.test(t)
    } catch (e) { return false }
  }
  async function mountAss(v, key) {
    let mod = null
    let meta = { fonts: [] }
    try {
      [mod, meta] = await Promise.all([
        ensureJassub(),
        api(`/api/stream/${versionId()}/fonts${kindParam()}`).catch(() => ({ fonts: [] })),
      ])
    } catch (e) {
      ctx.toast('ASS 渲染组件加载失败，可勾选「兼容」改用 VTT 字幕')
      return
    }
    if (videoEl.value !== v || ctx.getBurnOn() || subKind(selectedSub()) !== 'ass') return
    const sub = selectedSub() || {}
    const assUrl = sub.url || `/api/stream/${versionId()}/sub/${subIdx.value}.ass${kindParam()}`
    const fonts = (meta.fonts || []).map(f => f.url)
    // 无任何可用字体 + 含中日韩文本：libass 缺字形会显示不全，
    // 自动降级浏览器 VTT（系统字体渲染，保证可读；投放字体到 data/fonts/ 即恢复 ASS 样式）；
    // 本地临时 ASS 无服务端 VTT 变体 → 保持 JASSUB（宁可默认字体也不空白）
    if (!fonts.length && !isLocalSub(sub)) {
      const needCjk = await assNeedsCjk(assUrl)
      if (videoEl.value !== v || ctx.getBurnOn() || subKind(selectedSub()) !== 'ass') return
      if (needCjk) {
        autoVttSub.value = Number(subIdx.value)
        ctx.toast('未找到中文字体：已用浏览器 VTT 显示；把任意中文字体（woff2/ttf/ttc）放入 data/fonts/ 可恢复 ASS 样式')
        ctx.logEvt('ass:no-font-vtt', 'sub=' + subIdx.value)
        applySubs(true)
        return
      }
    }
    assFonts.value = fonts.length
    destroyAss()
    try {
      jassub = new mod.JASSUB({
        video: v,
        subUrl: assUrl,
        fonts,
        workerUrl: mod.workerUrl,
        wasmUrl: mod.wasmUrl,
        modernWasmUrl: mod.modernWasmUrl,
        timeOffset: subShift() + subDelay.value,
        // ASS 里指定字体缺失时用系统/内置兜底；无字体也不崩（libass 用内置 Liberation Sans）
        defaultFont: 'Liberation Sans',
      })
      assKey = key
      ctx.toast(fonts.length
        ? `ASS 字幕（样式渲染，${fonts.length} 个可用字体）`
        : (isLocalSub(sub)
          ? 'ASS 字幕：未找到内嵌/内置字体，文字可能走默认字体；可把中文字体（woff2/ttf/ttc）放入 data/fonts/'
          : 'ASS 字幕：未找到内嵌/内置字体，文字可能走默认字体；异常可勾选「兼容」或投放字体到 data/fonts/'))
      const inst = jassub
      Promise.resolve(inst.ready)
        .then(() => {
          if (jassub === inst) { try { inst.timeOffset = subShift() + subDelay.value } catch (e) { /* 忽略 */ } }
          ctx.logEvt('ass:ready', 'fonts=' + fonts.length)
        })
        .catch((e) => {
          ctx.toast('ASS 渲染初始化失败' + (isLocalSub(sub) ? '' : '，可勾选「兼容」改用 VTT 字幕'))
          ctx.logEvt('ass:error', String(e).slice(0, 160))
        })
    } catch (e) {
      ctx.toast('ASS 渲染初始化失败' + (isLocalSub(sub) ? '' : '，可勾选「兼容」改用 VTT 字幕'))
      ctx.logEvt('ass:init-error', String(e).slice(0, 160))
    }
  }

  // ================= PGS（libpgs） =================
  let pgs = null
  let pgsCanvas = null
  let pgsKey = ''

  function destroyPgs() {
    const inst = pgs
    pgs = null
    pgsKey = ''
    if (inst) { try { inst.dispose() } catch (e) { /* 忽略 */ } }
    // 自建 canvas（libpgs 拥有时只解除引用，不删元素；我们统一切断引用后移除）
    if (pgsCanvas) {
      try { pgsCanvas.remove() } catch (e) { /* 忽略 */ }
      pgsCanvas = null
    }
  }
  // PGS 画布：显式传入并对齐「画面区」（不含底部控件条），避免字幕坐标落进控件条被遮挡。
  // 注意：canvas 是 JS 动态创建，Vue scoped 样式不生效 → 内联样式（bottom 用 --pvb，全屏时归零）。
  function ensurePgsCanvas(v) {
    if (pgsCanvas && pgsCanvas.parentNode === v.parentNode) return pgsCanvas
    pgsCanvas = document.createElement('canvas')
    const st = pgsCanvas.style
    st.position = 'absolute'
    st.top = '0'
    st.left = '0'
    // 高度必须减去控件条（放 bottom 会被 height:100% 覆盖；100% 是 wrap 的 padding box）
    st.width = '100%'
    st.height = 'calc(100% - var(--pvb))'
    st.pointerEvents = 'none'
    st.objectFit = 'contain'
    v.insertAdjacentElement('afterend', pgsCanvas)
    return pgsCanvas
  }
  // PGS 客户端解码不可用（库加载失败/解码异常）→ 自动降级烧录（重开会话并提示）
  function fallbackBurnSub(msg) {
    if (forceBurn.value) return
    forceBurn.value = true
    ctx.toast((msg || 'PGS 客户端渲染不可用') + '，已自动切换为烧录模式（较耗 CPU）')
    ctx.logEvt('pgs:fallback-burn', String(msg || '').slice(0, 120))
    ctx.reload()
  }
  async function mountPgs(v, key) {
    let mod = null
    try {
      mod = await ensurePgs()
    } catch (e) {
      fallbackBurnSub('PGS 渲染组件加载失败')
      return
    }
    if (videoEl.value !== v || ctx.getBurnOn() || subKind(selectedSub()) !== 'pgs') return
    destroyPgs()
    try {
      const inst = new mod.PgsRenderer({
        video: v,
        canvas: ensurePgsCanvas(v),
        subUrl: `/api/stream/${versionId()}/sub/${subIdx.value}.sup${kindParam()}`,
        workerUrl: mod.workerUrl,
        timeOffset: subShift() + subDelay.value,
        aspectRatio: 'contain',   // 与 video object-fit 一致
      })
      pgs = inst
      pgsKey = key
      // worker 加载失败可能既不 resolve 也不 reject（事件被吞）→ 超时兜底降级烧录
      let settled = false
      const timer = setTimeout(() => {
        if (!settled && pgs === inst) fallbackBurnSub('PGS 渲染超时')
      }, 20000)
      Promise.resolve(inst.ready)
        .then(() => {
          settled = true
          clearTimeout(timer)
          if (pgs === inst) { try { inst.timeOffset = subShift() + subDelay.value } catch (e) { /* 忽略 */ } }
          ctx.logEvt('pgs:ready', 'delay=' + subDelay.value)
        })
        .catch((e) => {
          settled = true
          clearTimeout(timer)
          if (pgs === inst) fallbackBurnSub('PGS 解码失败：' + String(e).slice(0, 80))
        })
    } catch (e) {
      fallbackBurnSub('PGS 渲染初始化失败')
    }
  }

  // ================= VTT 自绘层 =================
  let vttCues = []
  let vttLayer = null
  let vttKey = ''
  let vttSeq = 0
  let vttLoopRvfc = 0
  let vttLoopTimer = 0
  let vttRO = null
  let vttLastKey = ''
  const vttNativeFallback = ref(false)
  let vttAnchor = 'inside'   // 当前锚定：inside 画面内 / outside 下黑边

  function applySubStyle() {
    if (!vttLayer) return
    vttLayer.className = `sub-layer bg-${subStyle.value.bg} ol-${subStyle.value.outline} anchor-${vttAnchor}`
  }
  function onSubStyleChange() {
    try {
      localStorage.setItem('jzmedia.subStyle', JSON.stringify({
        bg: subStyle.value.bg, outline: subStyle.value.outline,
        pos: subStyle.value.pos, size: subStyle.value.size,
      }))
    } catch (e) { /* 忽略 */ }
    applySubStyle()
    syncSubLayerRect()   // 位置/字号改动立即重排
    if (vttLayer) vttRender()
  }
  // 自绘图层：定位到视频「画面区」（contain 内接矩形，不进黑边）；字号随画面高缩放
  function ensureSubLayer(v) {
    if (vttLayer && vttLayer.parentNode) return vttLayer
    vttLayer = document.createElement('div')
    vttLayer.className = 'sub-layer'
    vttLayer.setAttribute('aria-hidden', 'true')
    v.parentNode.insertBefore(vttLayer, v.nextSibling)
    applySubStyle()
    return vttLayer
  }
  function syncSubLayerRect() {
    const v = videoEl.value
    const layer = vttLayer
    if (!v || !layer || !layer.parentNode || !v.parentNode) return
    const cr = v.parentNode.getBoundingClientRect()
    const vr = v.getBoundingClientRect()
    if (!cr.width || !vr.width || !vr.height) return
    const left = vr.left - cr.left
    const top = vr.top - cr.top
    const w = vr.width
    const h = vr.height
    let picTop = 0
    let picW = w
    let picH = h
    const vw = Number(v.videoWidth) || 0
    const vh = Number(v.videoHeight) || 0
    if (vw > 0 && vh > 0) {
      const ar = vw / vh
      const boxAr = w / h
      if (ar > boxAr) {          // 画面更宽：上下黑边
        picH = w / ar
        picTop = (h - picH) / 2
      } else if (ar < boxAr) {   // 画面更高：左右黑边
        picW = h * ar
      }
    }
    const barBottom = Math.max(0, Math.round(h - picTop - picH))
    const fontPx = subFontPx(picH, subStyle.value.size)
    vttAnchor = pickSubAnchor(barBottom, fontPx, subStyle.value.pos)
    layer.style.fontSize = fontPx + 'px'
    if (vttAnchor === 'outside') {
      // 黑边模式（mpv sub-use-margins 同款）：整元素高度做定位，字底距屏幕底自适应；
      // 多行时允许“一行画面内一行黑边”，底部永不被裁
      layer.style.left = left + 'px'
      layer.style.top = top + 'px'
      layer.style.width = w + 'px'
      layer.style.height = h + 'px'
      layer.style.setProperty('--sub-pad', subBarPad(barBottom) + 'px')
    } else {
      // 画面内：约束在 contain 内接矩形（左右/上下黑边都不进），字号随画面高缩放
      layer.style.left = (left + (w - picW) / 2) + 'px'
      layer.style.top = (top + picTop) + 'px'
      layer.style.width = picW + 'px'
      layer.style.height = picH + 'px'
      layer.style.setProperty('--sub-pad', subInnerPad(picH) + 'px')
    }
    applySubStyle()
  }
  function startVttRO(v) {
    stopVttRO()
    if (typeof ResizeObserver === 'undefined' || !v || !v.parentNode) return
    vttRO = new ResizeObserver(() => syncSubLayerRect())
    try { vttRO.observe(v.parentNode); vttRO.observe(v) } catch (e) { /* 忽略 */ }
  }
  function stopVttRO() {
    if (vttRO) { try { vttRO.disconnect() } catch (e) { /* 忽略 */ } vttRO = null }
  }
  // 渲染循环：优先 requestVideoFrameCallback（帧级），否则 100ms 轮询兜底
  function startVttLoop(v) {
    stopVttLoop()
    if (!v) return
    if (typeof v.requestVideoFrameCallback === 'function') {
      const step = () => {
        if (!vttLayer) return
        vttRender()
        try { vttLoopRvfc = v.requestVideoFrameCallback(step) } catch (e) { vttLoopRvfc = 0 }
      }
      try { vttLoopRvfc = v.requestVideoFrameCallback(step) } catch (e) { vttLoopRvfc = 0 }
    } else {
      vttLoopTimer = setInterval(vttRender, 100)
    }
  }
  function stopVttLoop() {
    const v = videoEl.value
    if (vttLoopRvfc && v && typeof v.cancelVideoFrameCallback === 'function') {
      try { v.cancelVideoFrameCallback(vttLoopRvfc) } catch (e) { /* 忽略 */ }
    }
    vttLoopRvfc = 0
    if (vttLoopTimer) { clearInterval(vttLoopTimer); vttLoopTimer = 0 }
  }
  // 命中判断：片内时间 + media_start + 用户延迟 落在 cue 源时间区间
  function vttRender() {
    const v = videoEl.value
    const layer = vttLayer
    if (!v || !layer) return
    const off = (subShift() + subDelay.value) * 1000
    const t = (Number.isFinite(v.currentTime) ? v.currentTime : 0) * 1000 + off
    const act = activeCues(vttCues, t)   // 命中判定纯函数（评审 R13-Q4）
    const key = act.map(c => c.start + ':' + c.end).join(',')
    if (key === vttLastKey) return
    vttLastKey = key
    layer.textContent = ''
    for (const c of act) {
      const d = document.createElement('div')
      d.className = 'sub-cue' + (c.align ? ' ta-' + c.align : '')
      d.textContent = c.text
      layer.appendChild(d)
    }
  }
  function clearVttDom() {
    stopVttLoop()
    stopVttRO()
    const v = videoEl.value
    if (v) v.querySelectorAll('track').forEach(t => t.remove())
    const l = vttLayer
    vttLayer = null
    if (l) { try { l.remove() } catch (e) { /* 忽略 */ } }
  }
  function destroyVtt() {
    vttSeq++
    vttKey = ''
    vttLastKey = ''
    vttCues = []
    vttNativeFallback.value = false
    clearVttDom()
  }
  // 所选字幕是否走文本（自绘）渲染：VTT 本体 / ASS 勾选兼容 / ASS 无字体自动降级。
  // 本地临时 ASS 无服务端 VTT 变体，不走兼容降级（保持 JASSUB 渲染）。
  function isVttKind() {
    const s = selectedSub()
    const k = subKind(s)
    if (isLocalSub(s)) return k === 'vtt'
    return k === 'vtt' || (k === 'ass' && (compatSub.value
      || autoVttSub.value === Number(subIdx.value)))
  }
  function isVttSelected() {
    // 原生 <track> 兜底时不显示外观控件（改了也不生效，评审 B8/R13-B8）
    if (ctx.getBurnOn() || vttNativeFallback.value) return false
    return isVttKind()
  }
  // 「外观」设置行可见性（复用以 isVttSelected 的同一判定）
  const subIsVtt = computed(() => isVttSelected())
  async function mountVttLayer(v, key) {
    const seq = ++vttSeq
    // 同轨重复调用（会话重载/延迟调整）不重拉：偏移在渲染时叠加
    if (vttKey === key && vttLayer && vttCues.length && !vttNativeFallback.value) {
      startVttLoop(v)
      startVttRO(v)
      syncSubLayerRect()
      vttRender()
      return
    }
    const sub = selectedSub() || {}
    const url = sub.url || `/api/stream/${versionId()}/sub/${subIdx.value}.vtt${kindParam()}`
    let text = typeof sub.text === 'string' ? sub.text : ''   // 本地字幕：已在加载期解码（含 GBK 兜底）
    if (!text) {
      try {
        const r = await fetch(url)
        if (!r.ok) throw new Error('http ' + r.status)
        text = await r.text()
      } catch (e) { text = '' }
    }
    if (seq !== vttSeq || videoEl.value !== v || !isVttSelected()) return
    const cues = text ? parseVtt(text) : []
    clearVttDom()
    vttKey = key
    vttCues = cues
    vttLastKey = ''
    vttNativeFallback.value = false
    if (!cues.length) {
      // 解析失败/空轨：回退原生 <track>（全局 ::cue 兜底样式已去默认黑底）
      vttNativeFallback.value = true
      const tr = document.createElement('track')
      tr.kind = 'subtitles'
      tr.src = url
      tr.default = true
      v.appendChild(tr)
      return
    }
    ensureSubLayer(v)
    startVttRO(v)
    startVttLoop(v)
    syncSubLayerRect()
    vttRender()
  }

  // ================= 渲染器注册表 + 分发 =================
  const SUB_RENDERERS = {
    ass: {
      alive: () => !!jassub, key: () => assKey, mount: mountAss,
      reuse: (toff) => { try { jassub.timeOffset = toff } catch (e) { /* 忽略 */ } },
    },
    pgs: {
      alive: () => !!pgs, key: () => pgsKey, mount: mountPgs,
      reuse: (toff) => { try { pgs.timeOffset = toff } catch (e) { /* 忽略 */ } },
    },
  }
  // 应用当前所选字幕（会话重载/换视频元素/切轨共用）：先拆旧层再按类型装新层。
  // 文本(VTT 自绘)/ASS(PGS) 全部客户端渲染——切字幕不重开会话、不转码；仅 VobSub（burn）走烧录。
  // 三类都吃 subShift()+subDelay 偏移（会话时间轴↔字幕绝对时间轴对齐）。
  async function applySubs(force) {
    const v = videoEl.value
    if (!v) return
    const sel = selectedSub()
    const kind = ctx.getBurnOn() ? 'burn' : subKind(sel)
    const subToken = isLocalSub(sel) ? sel.localId : subIdx.value
    const key = `${versionId()}:${videoKey.value}:${subToken}:${compatSub.value ? 'v' : 'a'}`
    const toff = subShift() + subDelay.value
    if (jassub && (force || kind !== 'ass' || assKey !== key)) destroyAss()
    if (pgs && (force || kind !== 'pgs' || pgsKey !== key)) destroyPgs()
    if (kind === 'none' || kind === 'burn') { destroyVtt(); return }
    if (kind === 'vtt' || isVttKind()) {
      // 原生 <track> 兜底中：同轨无需重挂；换轨（含切到本地字幕）必须先清旧 track 再重挂
      if (vttNativeFallback.value) {
        if (vttKey === key) return
        destroyVtt()
      }
      await mountVttLayer(v, key)
      return
    }
    destroyVtt()
    // 渲染器注册表（评审 R13-Q2）：{alive,key,mount,reuse} → 新增渲染器只需加一条
    const r = SUB_RENDERERS[kind]
    if (!r) return
    if (r.alive() && r.key() === key) { r.reuse(toff); return }
    await r.mount(v, key)
  }
  // 延迟步进：ASS/PGS 走 timeOffset，VTT 自绘在渲染时叠加 → 立即重绘
  function shiftSubDelay(d) {
    const x = Math.round(Math.max(-10, Math.min(10, subDelay.value + d)) * 10) / 10
    subDelay.value = x
    try { localStorage.setItem(subDelayKey.value, String(x)) } catch (e) { /* 忽略 */ }
    const toff = subShift() + x
    if (jassub) { try { jassub.timeOffset = toff } catch (e) { /* 忽略 */ } }
    if (pgs) { try { pgs.timeOffset = toff } catch (e) { /* 忽略 */ } }
    if (vttLayer) vttRender()
  }
  // 视频元素事件：VTT 自绘层随 timeupdate/seeked 重绘、metadata 就绪后重排
  function attachVideo(v) {
    if (!v) return
    v.addEventListener('timeupdate', vttRender)
    v.addEventListener('seeked', vttRender)
    v.addEventListener('loadedmetadata', syncSubLayerRect)
  }
  function detachVideo(v) {
    if (!v) return
    v.removeEventListener('timeupdate', vttRender)
    v.removeEventListener('seeked', vttRender)
    v.removeEventListener('loadedmetadata', syncSubLayerRect)
  }
  // 调试快照字段（PlayerModal debugSnapshot / window.__jzPlayer 用）
  function debugInfo() {
    return {
      ass: { active: !!jassub, fonts: assFonts.value, compat: compatSub.value,
             kind: subKind(subs.value[subIdx.value]) },
      pgs: { active: !!pgs, delay: subDelay.value, force_burn: forceBurn.value },
    }
  }
  function destroyAll() {
    destroyAss()
    destroyPgs()
    destroyVtt()
  }
  // 卸载清理：销毁三类渲染器并回收本地字幕 blob URL
  function dispose() {
    destroyAll()
    for (const s of localSubs.value) revokeLocalUrl(s)
    localSubs.value = []
  }

  return {
    // 状态
    subs, subIdx, localSubs, subStyle, subDelay, compatSub, autoVttSub,
    forceBurn, vttNativeFallback, assFonts,
    subIsAss, subIsVtt, subDelayText, subDelayVisible, undoDegradeDisabled,
    // 查询
    selectedSub, isLocalSub, imageSubSelected,
    // 会话/事件
    syncForSession, applySubs, onSubChange, onLoadSubFile, removeLocalSubs,
    onSubStyleSet, onCompatSet, onCompatChange, undoDegrade, shiftSubDelay,
    // 视频元素事件 + 调试
    attachVideo, detachVideo, debugInfo,
    assInstance: () => jassub, pgsInstance: () => pgs,
    // 生命周期
    destroyAss, destroyPgs, destroyVtt, destroyAll, dispose,
  }
}
