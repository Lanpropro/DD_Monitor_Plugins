<script setup>
import { computed } from 'vue'
import { useData, withBase } from 'vitepress'

const { page } = useData()
const items = [
  { label: '首页', link: '/', file: 'index.md', icon: 'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z' },
  { label: '软件', link: '/software', file: 'software.md', icon: 'M3 4h18v13H3ZM8 21h8m-4-4v4' },
  { label: '插件', link: '/plugins/', file: 'plugins/', icon: 'M9 3H4v6a3 3 0 1 1 0 6v6h6a3 3 0 1 1 6 0h5v-6a3 3 0 1 0 0-6V3h-6a3 3 0 1 1-6 0Z' },
  { label: '下载', link: '/download', file: 'download.md', icon: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5' },
  { label: '帮助', link: '/guide', file: 'guide.md', icon: 'M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15' }
]
const current = computed(() => items.find(item => item.file.endsWith('/')
  ? page.value.relativePath.startsWith(item.file)
  : page.value.relativePath === item.file)?.link)
</script>

<template>
  <nav class="dock-nav" aria-label="主要导航">
    <a v-for="item in items" :key="item.link" :href="withBase(item.link)" class="dock-item"
      :aria-current="current === item.link ? 'page' : undefined">
      <span class="dock-icon"><svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="item.icon" /></svg></span>
      <span class="dock-label">{{ item.label }}</span>
    </a>
  </nav>
</template>
