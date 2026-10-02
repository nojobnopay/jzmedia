import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'

// Render the actual child components too: extracting a form must not weaken setup coverage.
export async function loadSfc(filename, overrides = {}) {
  const url = filename instanceof URL ? filename : new URL(filename, import.meta.url)
  const { descriptor } = parse(readFileSync(url, 'utf8'), { filename: url.pathname })
  const compiled = compileScript(descriptor, { id: 'setup-test', inlineTemplate: true, genDefaultAs: 'component' })
  const modules = []
  let code = compiled.content
  const declarations = [...code.matchAll(/import\s+(\{[^}]+\}|[\w$]+)\s+from\s+['"]([^'"]+)['"];?/g)]
  for (const match of declarations) {
    const [full, names, source] = match
    const target = source.startsWith('.') ? new URL(source, url) : source
    const mod = overrides[source] || (source.endsWith('.vue')
      ? { default: await loadSfc(target, overrides) } : await import(target))
    const i = modules.push(mod) - 1
    const binding = names.startsWith('{') ? names.replace(/\bas\b/g, ':') : names
    code = code.replace(full, 'const ' + binding + ' = modules[' + i + ']' + (names.startsWith('{') ? '' : '.default'))
  }
  code = code.replace(/import\s+['"][^'"]+\.css['"];?/g, '')
  return new Function('modules', code + '\nreturn component')(modules)
}
