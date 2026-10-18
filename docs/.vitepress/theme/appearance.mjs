// Keep VitePress's persisted theme state and its existing switch buttons.
export function createAppearanceToggle(isDark, nextTick) {
  let transition
  let bounce
  return async event => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const button = event?.currentTarget
    const rect = button?.getBoundingClientRect()
    bounce?.cancel()
    if (!reduced && button?.animate) {
      bounce = button.animate([
        { transform: 'scale(1)' }, { transform: 'scale(.92)', offset: .2 },
        { transform: 'scale(1.06)', offset: .5 }, { transform: 'scale(.99)', offset: .78 },
        { transform: 'scale(1)' }
      ], { duration: 400, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' })
    }
    // Finish the previous state update before a rapid second click takes a snapshot.
    if (transition) {
      transition.skipTransition()
      await transition.updateCallbackDone.catch(() => {})
    }
    const update = async () => { isDark.value = !isDark.value; await nextTick() }
    if (reduced || !document.startViewTransition) { await update(); return }
    const x = rect ? rect.left + rect.width / 2 : innerWidth / 2
    const y = rect ? rect.top + rect.height / 2 : 32
    const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))
    const current = document.startViewTransition(update)
    transition = current
    try {
      await current.ready
      document.documentElement.animate([
        { clipPath: `circle(0px at ${x}px ${y}px)` },
        { clipPath: `circle(${radius}px at ${x}px ${y}px)` }
      ], { duration: 560, easing: 'cubic-bezier(0.23, 1, 0.32, 1)', pseudoElement: '::view-transition-new(root)' })
      await current.finished
    } catch { /* A skipped transition still applies the requested theme. */ }
    finally { if (transition === current) transition = undefined }
  }
}
