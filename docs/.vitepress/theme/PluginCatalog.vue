<script setup>
import { computed, ref } from 'vue'
import { withBase } from 'vitepress'
import plugins from '../../data/plugins.json'

const query = ref('')
const category = ref('全部')
const filtered = computed(() => plugins.filter(p =>
  (category.value === '全部' || p.category === category.value) &&
  [p.name, p.id, ...p.platforms].join(' ').toLowerCase().includes(query.value.trim().toLowerCase())
))
</script>

<template>
  <div class="catalog-controls">
    <label class="catalog-search">搜索插件或平台
      <input v-model="query" type="search" placeholder="例如：抖音、YouTube" />
    </label>
    <div class="filters" role="group" aria-label="插件分类">
      <button v-for="item in ['全部', '国内直播', '海外直播', '比赛二路']" :key="item"
        :aria-pressed="category === item" @click="category = item">{{ item }}</button>
    </div>
  </div>
  <p class="result-count" aria-live="polite">{{ filtered.length }} 个插件</p>
  <div class="plugin-grid">
    <article v-for="p in filtered" :key="p.id" class="plugin-card">
      <div class="card-top">
        <div class="platform-icons"><img v-for="(icon, i) in p.icons" :key="icon"
          :src="withBase('/platforms/' + icon)" :alt="p.platforms[i]" width="32" height="32" /></div>
        <span class="version">v{{ p.version }}</span>
      </div>
      <h2><a :href="withBase('/plugins/' + p.id)">{{ p.name }}</a></h2>
      <p>{{ p.description }}</p>
      <div class="platform-tags"><span v-for="platform in p.platforms" :key="platform">{{ platform }}</span></div>
      <ul><li v-for="feature in p.features" :key="feature">{{ feature }}</li></ul>
      <p class="requirement">需配套更新的软件 · <a :href="withBase('/download#插件兼容性')">查看兼容要求</a></p>
      <div class="card-actions"><a class="action primary" :href="p.download">下载 ZIP</a>
        <a class="action secondary" :href="withBase('/plugins/' + p.id)">使用说明</a></div>
    </article>
  </div>
  <div v-if="!filtered.length" class="empty-result"><p>没有匹配的插件。试试平台名称，或清空筛选。</p>
    <button class="action secondary" @click="query = ''; category = '全部'">显示全部插件</button></div>
</template>
