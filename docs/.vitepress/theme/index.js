import DefaultTheme from 'vitepress/theme'
import DocStep from './components/DocStep.vue'
import DocFigure from './components/DocFigure.vue'
import DocVideo from './components/DocVideo.vue'
import DocDiagram from './components/DocDiagram.vue'
import { tokenize } from '../search.mjs'
import './style.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app, siteData }) {
    if (import.meta.env.PROD) {
      siteData.value.themeConfig.search.options.miniSearch.options.tokenize = tokenize
    }
    for (const [name, component] of Object.entries({ DocStep, DocFigure, DocVideo, DocDiagram })) {
      app.component(name, component)
    }
  },
}
