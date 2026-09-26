import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import { parseSmbInput, smbUrlOf } from '../src/smb.js'

test('media library form initializes and renders before any connection is loaded', async () => {
  // Compiling alone misses watchers that read reactive state before initialization.
  const filename = new URL('../src/components/LibrariesPanel.vue', import.meta.url)
  const { descriptor } = parse(readFileSync(filename, 'utf8'))
  const compiled = compileScript(descriptor, {
    id: 'settings-setup', inlineTemplate: true, genDefaultAs: 'component',
  })
  const code = compiled.content.replace(/import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]/g,
    (_, names, source) => `const {${names.replace(/\bas\b/g, ':')}} = imports[${JSON.stringify(source)}]`)
  const imports = {
    vue: Vue,
    '../api.js': { api: () => { throw new Error('Rendering must not send API requests') } },
    '../libraries.js': { loadLibs: async () => {} },
    '../smb.js': { parseSmbInput, smbUrlOf },
  }
  const component = new Function('imports', code + '\nreturn component')(imports)
  const html = await renderToString(Vue.createSSRApp(component))
  assert.match(html, /媒体库列表/)
  assert.match(html, /创建媒体库/)
  assert.match(html, /jz-lib-name/)
})
