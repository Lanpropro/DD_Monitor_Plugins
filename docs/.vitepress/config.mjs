import { defineConfig } from 'vitepress'

export default defineConfig({
  lang: 'zh-CN',
  title: 'DD 监控室 CE',
  description: '多画面直播观看工具，软件介绍、下载与独立插件。',
  base: '/DD_Monitor_Plugins/',
  cleanUrls: true,
  appearance: 'dark',
  head: [['link', { rel: 'icon', href: '/DD_Monitor_Plugins/logo.svg' }]],
  themeConfig: {
    logo: '/logo.svg',
    siteTitle: 'DD 监控室 CE',
    nav: [
      { text: '软件介绍', link: '/software' },
      { text: '插件', link: '/plugins/' },
      { text: '下载', link: '/download' },
      { text: '安装帮助', link: '/guide' }
    ],
    sidebar: {
      '/plugins/': [{ text: '插件', items: [
        { text: '所有插件', link: '/plugins/' },
        { text: '国内直播平台', link: '/plugins/domestic_live' },
        { text: 'Twitch 与 YouTube', link: '/plugins/global_live' },
        { text: '比赛二路同步', link: '/plugins/match_sync' },
        { text: '安装与升级', link: '/guide' }
      ] }]
    },
    search: { provider: 'local', options: { locales: { root: { translations: {
      button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
      modal: { noResultsText: '没有找到相关内容', resetButtonTitle: '清空搜索', footer: {
        selectText: '选择', navigateText: '切换', closeText: '关闭'
      } }
    } } } } },
    outline: { label: '本页内容' },
    docFooter: { prev: '上一页', next: '下一页' },
    darkModeSwitchLabel: '外观',
    sidebarMenuLabel: '目录',
    returnToTopLabel: '返回顶部',
    socialLinks: [{ icon: 'github', link: 'https://github.com/Lanpropro/DD_Monitor_Plugins' }],
    footer: {
      message: '基于 DD监控室的二次开发 · LGPL-2.1 · 平台标识仅用于来源识别',
      copyright: 'DD 监控室 CE · 软件与插件分别维护'
    }
  }
})
