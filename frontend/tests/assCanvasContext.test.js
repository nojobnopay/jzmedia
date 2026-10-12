import test from 'node:test'
import assert from 'node:assert/strict'
import { useCompositedAssContexts } from '../src/assCanvasContext.js'

function fakeCanvas() {
  return class Canvas {
    getContext(...args) { return { canvas: this, args } }
  }
}

test('ASS 上下文走常规合成，同时保留透明度和 WebGL 选项', () => {
  const Canvas = fakeCanvas()
  useCompositedAssContexts(Canvas)
  const canvas = new Canvas()
  const attributes = { alpha: true, premultipliedAlpha: true,
    preserveDrawingBuffer: false, desynchronized: true }
  for (const type of ['webgl', 'webgl2']) {
    const context = canvas.getContext(type, attributes)
    assert.equal(context.canvas, canvas)
    assert.deepEqual(context.args, [type, { ...attributes, desynchronized: false }])
  }
  assert.equal(attributes.desynchronized, true, '不修改调用者的选项对象')
})

test('ASS 的 Canvas2D 兜底同样关闭低延迟直出，未传选项时保持默认透明度', () => {
  const Canvas = fakeCanvas()
  useCompositedAssContexts(Canvas)
  assert.deepEqual(new Canvas().getContext('2d').args, ['2d', { desynchronized: false }])
})

test('上下文策略只作用于传入的 worker Canvas，其他类型保持原调用', () => {
  const Canvas = fakeCanvas(), OtherCanvas = fakeCanvas()
  useCompositedAssContexts(Canvas)
  const attributes = { desynchronized: true }
  assert.deepEqual(new OtherCanvas().getContext('webgl2', attributes).args, ['webgl2', attributes])
  assert.deepEqual(new Canvas().getContext('bitmaprenderer', attributes).args, ['bitmaprenderer', attributes])
  assert.deepEqual(new Canvas().getContext('bitmaprenderer').args, ['bitmaprenderer'])
  assert.doesNotThrow(() => useCompositedAssContexts(undefined))
})
