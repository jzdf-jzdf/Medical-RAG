<script setup lang="ts">
import AccountMenu from './AccountMenu.vue'

export type PlatformApp = 'knowledge' | 'agent' | 'evaluation'

withDefaults(defineProps<{
  activeApp: PlatformApp
  ariaLabel?: string
  homeHref?: string
  accountAccent?: 'accent' | 'primary'
  hideAccountOnMobile?: boolean
}>(), {
  ariaLabel: '平台导航',
  homeHref: '/knowledge/',
  accountAccent: 'accent',
  hideAccountOnMobile: false,
})

const applications: Array<{ id: PlatformApp; label: string; href: string }> = [
  { id: 'knowledge', label: '知识空间', href: '/knowledge/' },
  { id: 'agent', label: '智能问答', href: '/agent/' },
  { id: 'evaluation', label: '评测中心', href: '/evaluation/' },
]
</script>

<template>
  <aside class="platform-sidebar-shell" :aria-label="ariaLabel">
    <a class="platform-brand" :href="homeHref" aria-label="返回知识空间">
      <span class="platform-brand__mark" aria-hidden="true">
        <svg viewBox="0 0 26 12" focusable="false">
          <path d="M0 0h2v12H0zM12 0H6C4.9 0 4 .9 4 2v8c0 1.1.9 2 2 2h6v-2H6V2h6zM15 0h2v5h7V0h2v12h-2V7h-7v5h-2z" />
        </svg>
      </span>
      <span class="platform-brand__copy">
        <strong>循证医学平台</strong>
        <small class="platform-brand__subtitle">ICH EVIDENCE OS</small>
      </span>
    </a>

    <nav class="platform-app-switcher" aria-label="平台应用切换">
      <a
        v-for="application in applications"
        :key="application.id"
        :href="application.href"
        :class="{ active: application.id === activeApp }"
        :aria-current="application.id === activeApp ? 'page' : undefined"
      >
        {{ application.label }}
      </a>
    </nav>

    <div class="platform-sidebar-content">
      <slot />
    </div>

    <AccountMenu :accent="accountAccent" :hide-on-mobile="hideAccountOnMobile" />
  </aside>
</template>

<style scoped>
.platform-sidebar-shell {
  box-sizing: border-box;
  width: var(--platform-sidebar-width, 244px);
  min-width: 0;
  min-height: var(--platform-sidebar-min-height, 100vh);
  padding: var(--platform-sidebar-padding, 26px 20px 22px);
  display: flex;
  flex-direction: column;
  color: var(--ink, #173b43);
  background: var(--platform-sidebar-background, #fbfcfc);
  border-right: var(--platform-sidebar-border-right, 1px solid var(--line, #d5e1df));
  border-bottom: var(--platform-sidebar-border-bottom, 0);
}

.platform-brand {
  width: 100%;
  display: var(--platform-brand-display, flex);
  align-items: center;
  gap: 12px;
  margin: var(--platform-brand-margin, 0 0 20px);
  padding: 4px;
  border-radius: 10px;
  color: inherit;
  background: transparent;
  text-decoration: none;
  transition: background-color .18s ease;
}

.platform-brand:hover { background: var(--primary-soft, var(--accent-soft, #e5f1ef)); }
.platform-brand:focus-visible,
.platform-app-switcher a:focus-visible { outline: 3px solid rgba(20, 115, 109, .3); outline-offset: 2px; }

.platform-brand__mark {
  width: 42px;
  height: 42px;
  display: grid;
  flex: 0 0 42px;
  place-items: center;
  border-radius: 11px;
  color: #fff;
  background: var(--primary, var(--accent, var(--teal, #14736d)));
  box-shadow: 0 7px 18px rgba(20, 115, 109, .18);
}

.platform-brand__mark svg { display: block; width: 26px; height: 12px; fill: currentColor; }
.platform-brand__copy { min-width: 0; }
.platform-brand__copy strong,
.platform-brand__copy small { display: block; }
.platform-brand__copy strong { font-size: 15px; line-height: 1.2; letter-spacing: .03em; }
.platform-brand__copy small {
  margin-top: 4px;
  color: #71878a;
  font: 600 8px/1.2 ui-monospace, SFMono-Regular, Consolas, monospace;
  letter-spacing: .12em;
}
.platform-brand__subtitle { display: var(--platform-brand-subtitle-display, block); }

.platform-app-switcher {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 3px;
  margin: var(--platform-switcher-margin, 0 0 28px);
  padding: 4px;
  border: 1px solid var(--line, #d5e1df);
  border-radius: 10px;
  background: #f0f4f3;
}

.platform-app-switcher a {
  min-width: 0;
  padding: 8px 2px;
  border-radius: 7px;
  color: #60787b;
  font-size: 10px;
  font-weight: 650;
  line-height: 1.3;
  text-align: center;
  text-decoration: none;
  transition: color .18s ease, background-color .18s ease, box-shadow .18s ease;
}

.platform-app-switcher a:hover { color: var(--primary, var(--accent, var(--teal, #14736d))); background: rgba(255, 255, 255, .72); }
.platform-app-switcher a.active { color: #0e5b57; background: #fff; box-shadow: 0 4px 12px rgba(34, 74, 72, .08); }
.platform-sidebar-content { min-height: 0; display: flex; flex: 1; flex-direction: column; }

@media (prefers-reduced-motion: reduce) {
  .platform-brand,
  .platform-app-switcher a { transition-duration: .01ms; }
}
</style>
