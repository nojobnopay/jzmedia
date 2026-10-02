import DefaultTheme from 'vitepress/theme'
import { h } from 'vue'
import DocStep from './components/DocStep.vue'
import DocFigure from './components/DocFigure.vue'
import DocVideo from './components/DocVideo.vue'
import DocDiagram from './components/DocDiagram.vue'
import PageMeta from './components/PageMeta.vue'
import { tokenize } from '../search.mjs'
import './style.css'

export default {
  extends: DefaultTheme,
  Layout() { return h(DefaultTheme.Layout, null, { 'doc-before': () => h(PageMeta) }) },
  enhanceApp({ app, siteData }) {
    if (import.meta.env.PROD) {
      siteData.value.themeConfig.search.options.miniSearch.options.tokenize = tokenize
    }
    for (const [name, component] of Object.entries({ DocStep, DocFigure, DocVideo, DocDiagram })) {
      app.component(name, component)
    }
  },
}
