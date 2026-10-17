import DefaultTheme from 'vitepress/theme'
import HomePage from './HomePage.vue'
import PluginCatalog from './PluginCatalog.vue'
import PluginInfo from './PluginInfo.vue'
import DockNav from './DockNav.vue'
import FeatureDemo from './FeatureDemo.vue'
import { h } from 'vue'
import './style.css'
import './dock.css'

export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, { 'layout-bottom': () => h(DockNav) })
  },
  enhanceApp({ app }) {
    app.component('HomePage', HomePage)
    app.component('PluginCatalog', PluginCatalog)
    app.component('PluginInfo', PluginInfo)
    app.component('FeatureDemo', FeatureDemo)
  }
}
