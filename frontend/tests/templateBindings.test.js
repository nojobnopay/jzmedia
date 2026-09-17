// 模板绑定静态检查（回归网）：模板里引用的标识符必须在 <script setup> 有绑定。
// 起因：Detail 拆出的 MovieEditPanel 模板残留父级 `m.tmdb_id`，编译通过但渲染报错、
// 面板不显示（2026-09-17 两次前端回归之一）。node:test 用 @vue/compiler-sfc 编译期元数据检查。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse as parseSfc, compileScript } from '@vue/compiler-sfc'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src')

// 模板编译期可用的全局/语法名（与 Vue 模板运行时一致的最小集）
const TEMPLATE_GLOBALS = new Set([
  'true', 'false', 'null', 'undefined', 'NaN', 'Infinity', 'this',
  '$event', '$attrs', '$slots', '$refs', '$el', '$emit', '$props', '$data',
  'Math', 'Number', 'String', 'Boolean', 'Array', 'Object', 'JSON', 'Date',
  'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'RegExp', 'Error', 'Map', 'Set',
  'encodeURIComponent', 'decodeURIComponent', 'console', 'window', 'document',
  '$router', '$route',
  'of', 'in', 'new', 'typeof', 'instanceof', 'void', 'delete', 'return',
])

function walkFs(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walkFs(p))
    else if (p.endsWith('.vue')) out.push(p)
  }
  return out
}

// 从模板表达式里提取“读取”的标识符：
// - 跳过属性名（.x）、对象键（{x: 或 , x:）、字符串字面量
// - 箭头函数参数视为局部名
function exprIdentifiers(expr, locals) {
  let src = String(expr)
    .replace(/'(?:[^'\\]|\\.)*'/g, "''")
    .replace(/"(?:[^"\\]|\\.)*"/g, '""')
    .replace(/`(?:[^`\\]|\\.)*`/g, '``')
  // 对象键（含引号键）
  src = src.replace(/([{,]\s*)(?:[A-Za-z_$][\w$]*|'[^']*'|"[^"]*")(\s*:)/g, '$1')
  // 箭头函数参数 → 局部
  const local = new Set(locals)
  const forParams = src.replace(/[()]/g, ' ')
  for (const m of forParams.matchAll(/([A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*)\s*=>/g)) {
    for (const part of m[1].split(',')) {
      const n = part.trim().split(/[:=]/)[0].trim()
      if (n) local.add(n)
    }
  }
  const ids = []
  for (const m of src.matchAll(/(?<![.$\w])([A-Za-z_$][\w$]*)(?![\w$])/g)) {
    const id = m[1]
    if (TEMPLATE_GLOBALS.has(id) || local.has(id)) continue
    ids.push(id)
  }
  return ids
}

function templateIdents(filename) {
  const { descriptor } = parseSfc(readFileSync(filename, 'utf-8'), { filename })
  if (!descriptor.scriptSetup) return []
  const s = compileScript(descriptor, { id: 'bindcheck' })
  const bindings = new Set(Object.keys(s.bindings || {}))
  // 简单起见：正则收集模板表达式 + v-for/v-slot 局部名（逐块近似，不漏报为主）
  const unbound = []
  const template = descriptor.template ? descriptor.template.content : ''
  // v-for 局部变量：文本扫描收集别名，避免误报
  const localNames = new Set()
  for (const m of template.matchAll(/v-for="\s*\(?([^)"]*?)\)?\s+(?:in|of)\s+/g)) {
    for (const part of m[1].split(',')) {
      const n = part.trim().split(/[:=]/)[0].trim()
      if (n) localNames.add(n)
    }
  }
  for (const m of template.matchAll(/v-slot(?::[^\s=]*)?\s*=\s*"([^"]*)"|#\w+\s*=\s*"([^"]*)"/g)) {
    const val = m[1] || m[2] || ''
    for (const idm of val.matchAll(/([A-Za-z_$][\w$]*)/g)) localNames.add(idm[1])
  }
  const scan = (re) => {
    for (const m of template.matchAll(re)) {
      const expr = m[1] || m[2] || ''
      for (const id of exprIdentifiers(expr, new Set([...bindings, ...localNames]))) {
        unbound.push(id)
      }
    }
  }
  scan(/\{\{([^}]*)\}\}/g)
  scan(/(?:v-(?:if|else-if|show|model(?:\.[\w.]+)?|html|text)|:[\w.-]+|@[\w.-]+)="([^"]*)"/g)
  scan(/(?:v-(?:if|else-if|show|model(?:\.[\w.]+)?|html|text)|:[\w.-]+|@[\w.-]+)='([^']*)'/g)
  return [...new Set(unbound)]
}

test('所有 .vue 模板的标识符均有 script setup 绑定', () => {
  const bad = []
  for (const f of walkFs(SRC)) {
    for (const id of templateIdents(f)) {
      bad.push(`${path.relative(SRC, f)}: '${id}'`)
    }
  }
  assert.deepEqual(bad, [], '模板存在未绑定标识符：\n' + bad.join('\n'))
})
