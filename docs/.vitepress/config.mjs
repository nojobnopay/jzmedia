import { defineConfig } from 'vitepress'
import { sourceExcludes, sidebar } from './public-pages.mjs'
import { tokenize, searchOptions, renderSearchContent } from './search.mjs'
import { mermaidFence } from './diagram-source.mjs'
import { finishBuild, staticMetadata } from '../scripts/artifacts.mjs'

export default defineConfig({
  lang: 'zh-CN',
  title: 'jzmedia 帮助',
  description: '从第一部内容入库到日常观看、片库管理与问题排查。',
  base: '/help/',
  cleanUrls: false,
  srcExclude: sourceExcludes(),
  outDir: '.vitepress/dist',
  ignoreDeadLinks: false,
  appearance: true,
  lastUpdated: false,
  vite: { publicDir: false, build: { sourcemap: false, assetsInlineLimit: 0 } },
  vue: { template: { transformAssetUrls: {
    includeAbsolute: true,
    tags: { img: ['src'], image: ['xlink:href', 'href'], video: ['src', 'poster'], source: ['src'],
      track: ['src'], DocFigure: ['src'], DocDiagram: ['src'], DocVideo: ['src', 'poster', 'captions'] },
  } } },
  markdown: { config: md => mermaidFence(md) },
  themeConfig: {
    nav: [
      { text: '帮助首页', link: '/' },
      { text: '开始使用', link: '/user-guide/onboarding.html' },
      { text: '解决问题', link: '/user-guide/troubleshooting.html' },
      { text: '开发参考', link: '/developer/README.html' },
    ],
    sidebar,
    outline: { level: [2, 3], label: '本页内容' },
    docFooter: { prev: '上一页', next: '下一页' },
    sidebarMenuLabel: '文档目录', returnToTopLabel: '回到顶部',
    darkModeSwitchLabel: '切换明暗主题',
    search: { provider: 'local', options: {
      _render: renderSearchContent,
      miniSearch: { options: { tokenize }, searchOptions },
      translations: {
        button: { buttonText: '搜索帮助', buttonAriaLabel: '搜索帮助' },
        modal: { displayDetails: '显示详细结果', resetButtonTitle: '清空搜索',
          backButtonTitle: '关闭搜索', noResultsText: '没有找到结果，请换一个关键词',
          footer: { selectText: '打开', navigateText: '切换', closeText: '关闭' } },
      },
    } },
    footer: { message: '离线可读 · 图片和示例以标注的演示环境与版本为准' },
  },
  transformHtml: staticMetadata,
  buildEnd: finishBuild,
})
