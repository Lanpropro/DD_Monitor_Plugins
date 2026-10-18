<script setup>
import DefaultTheme from 'vitepress/theme'
import { useData } from 'vitepress'
import { nextTick, provide } from 'vue'
import DockNav from './DockNav.vue'
import { createAppearanceToggle } from './appearance.mjs'

const Layout = DefaultTheme.Layout
const { isDark } = useData()
provide('toggle-appearance', createAppearanceToggle(isDark, nextTick))
</script>

<template>
  <Layout><template #nav-bar-content-before><DockNav /></template></Layout>
</template>

<style>
::view-transition-old(root), ::view-transition-new(root) { animation: none; mix-blend-mode: normal; }
::view-transition-old(root) { z-index: 1; }
::view-transition-new(root) { z-index: 2; }
.VPSwitchAppearance .check {
  transition: transform 400ms var(--ease-out) !important;
  transition-timing-function: linear(0, .32 8%, .79 20%, 1.03 30%, 1.01 46%, 1) !important;
}
/* Expose the same switch directly on narrow screens as well. */
.VPNavBarAppearance { display: flex !important; align-items: center; }
.VPNavBarAppearance .VPSwitchAppearance { margin-inline: 8px; }
@media (max-width: 400px) {
  .VPNavBar > .wrapper { padding-inline: 12px; }
  .VPNavBarTitle .title { font-size: 14px; }
  .VPNavBarAppearance .VPSwitchAppearance { margin-inline: 4px; }
}
@media (prefers-reduced-motion: reduce) {
  .VPSwitchAppearance .check { transition: none !important; }
}
</style>
