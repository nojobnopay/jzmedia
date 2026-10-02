import { createApp } from 'vue'
import './styles/tokens.css'
import './styles/mediaPages.css'
import './styles/browse.css'
import App from './App.vue'
import router from './router.js'
import { loadPrefs, applyPrefs } from './prefs.js'

applyPrefs(loadPrefs())
createApp(App).use(router).mount('#app')
