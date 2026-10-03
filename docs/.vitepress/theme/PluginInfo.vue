<script setup>
import { computed } from 'vue'
import { withBase } from 'vitepress'
import plugins from '../../data/plugins.json'
const props = defineProps({ id: { type: String, required: true } })
const plugin = computed(() => plugins.find(p => p.id === props.id))
</script>

<template>
  <div v-if="plugin" class="plugin-info">
    <div class="card-top"><div class="platform-icons"><img v-for="(icon, i) in plugin.icons" :key="icon"
      :src="withBase('/platforms/' + icon)" :alt="plugin.platforms[i]" width="32" height="32" /></div>
      <span class="version">v{{ plugin.version }}</span></div>
    <p>{{ plugin.description }}</p>
    <p>插件 ID：<code>{{ plugin.id }}</code> · 支持 {{ plugin.platforms.join('、') }}</p>
    <div class="card-actions"><a class="action primary" :href="plugin.download">下载 v{{ plugin.version }} ZIP</a>
      <a class="action secondary" :href="`https://github.com/Lanpropro/DD_Monitor_Plugins/tree/main/plugins/${plugin.id}`">插件源码</a></div>
    <p class="requirement">安装前请先阅读<a :href="withBase('/download#插件兼容性')">软件兼容要求</a>，公开 v0.2 安装包缺少插件所需模块。</p>
    <details><summary>安装包 SHA256</summary><code class="checksum">{{ plugin.sha256 }}</code></details>
  </div>
</template>
