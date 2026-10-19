import DefaultTheme from 'vitepress/theme'
import HomePage from './HomePage.vue'
import PluginCatalog from './PluginCatalog.vue'
import PluginInfo from './PluginInfo.vue'
import ThemeLayout from './ThemeLayout.vue'
import FeatureDemo from './FeatureDemo.vue'
import './style.css'
import './dock.css'

export default {
  extends: DefaultTheme,
  Layout: ThemeLayout,
  enhanceApp({ app }) {
    app.component('HomePage', HomePage)
    app.component('PluginCatalog', PluginCatalog)
    app.component('PluginInfo', PluginInfo)
    app.component('FeatureDemo', FeatureDemo)
  }
}
