import DefaultTheme from 'vitepress/theme'
import HomePage from './HomePage.vue'
import PluginCatalog from './PluginCatalog.vue'
import PluginInfo from './PluginInfo.vue'
import './style.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('HomePage', HomePage)
    app.component('PluginCatalog', PluginCatalog)
    app.component('PluginInfo', PluginInfo)
  }
}
