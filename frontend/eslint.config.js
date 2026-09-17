// 前端 lint（评审 R14-Q3/R04-Q6）：先上「防事故」最小集，不追求风格规则。
// 目标：未定义变量（含 Vue 模板插值）、未用变量、误留 console 三类问题拦截。
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/base'],
  {
    files: ['**/*.{js,mjs,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        // 浏览器
        window: 'readonly', document: 'readonly', location: 'readonly',
        localStorage: 'readonly', fetch: 'readonly', setTimeout: 'readonly',
        clearTimeout: 'readonly', setInterval: 'readonly', clearInterval: 'readonly',
        requestAnimationFrame: 'readonly', cancelAnimationFrame: 'readonly',
        IntersectionObserver: 'readonly', ResizeObserver: 'readonly',
        MediaSource: 'readonly', Blob: 'readonly', File: 'readonly',
        FormData: 'readonly', AbortController: 'readonly', Event: 'readonly',
        CustomEvent: 'readonly', navigator: 'readonly', performance: 'readonly',
        structuredClone: 'readonly', atob: 'readonly', btoa: 'readonly',
        XMLHttpRequest: 'readonly', URLSearchParams: 'readonly', sessionStorage: 'readonly',
        URL: 'readonly', Headers: 'readonly', Request: 'readonly', Response: 'readonly',
        TextDecoder: 'readonly', TextEncoder: 'readonly', FileReader: 'readonly',
        Worker: 'readonly', Image: 'readonly', Audio: 'readonly', crypto: 'readonly',
        history: 'readonly', screen: 'readonly', matchMedia: 'readonly',
        // 测试（node:test）
        process: 'readonly', console: 'readonly',
        // Vue 编译宏
        defineProps: 'readonly', defineEmits: 'readonly', defineExpose: 'readonly',
        defineOptions: 'readonly', defineSlots: 'readonly', defineModel: 'readonly',
        withDefaults: 'readonly'
      }
    },
    rules: {
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
      'no-console': 'warn',
      'no-empty': ['warn', { allowEmptyCatch: true }],
      'no-undef': 'error',
      // 最小集外的新规则（eslint 10 recommended）暂关：存量代码防御性赋值风格，不做大规模改写
      'no-useless-assignment': 'off',
      'preserve-caught-error': 'off'
    }
  }
]
