import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import { useTvBindings } from '../src/useTvBindings.js'

function componentFromFile(filename, imports) {
  const { descriptor } = parse(readFileSync(filename, 'utf8'))
  const compiled = compileScript(descriptor, { id: 'tv-bindings-setup', inlineTemplate: true,
    genDefaultAs: 'component' })
  const code = compiled.content.replace(/import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]/g,
    (_, names, source) => `const {${names.replace(/\bas\b/g, ':')}} = imports[${JSON.stringify(source)}]`)
    .replace(/import\s+(\w+)\s+from\s*['"]([^'"]+)['"]/g,
      (_, name, source) => `const ${name} = imports[${JSON.stringify(source)}].default`)
  return new Function('imports', code + '\nreturn component')(imports)
}

test('directory binding dialog initializes and renders with its real composable', async () => {
  const filename = new URL('../src/components/TvBindingsDialog.vue', import.meta.url)
  const help = componentFromFile(new URL('../src/components/HelpLink.vue', import.meta.url), { vue: Vue })
  const imports = { vue: Vue, '../useTvBindings.js': { useTvBindings },
    '../useFocusTrap.js': { useFocusTrap() {} }, './HelpLink.vue': { default: help } }
  const component = componentFromFile(filename, imports)
  const app = Vue.createSSRApp(component, { libraryId: 3 })
  app.component('RouterLink', { template: '<a><slot /></a>' })
  const context = {}
  await renderToString(app, context)
  assert.match(context.teleports.body, /剧集归属与季号/)
  assert.match(context.teleports.body, /选择目录/)
  assert.match(context.teleports.body, /没有符合条件的剧集目录/)
})
