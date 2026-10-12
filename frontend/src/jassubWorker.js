// 按依赖顺序先设置画布策略，再注册原 JASSUB RPC worker；避免异步加载期间丢请求。
import './assCanvasContext.js'
import 'jassub/dist/worker/worker.js'
