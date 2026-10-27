<script setup lang="ts">
import { computed } from 'vue'
import { session, signOut } from '../auth'

const initial = computed(() => session.value?.displayName.slice(0, 1).toUpperCase() || 'U')
</script>

<template>
  <section v-if="session" class="account-menu" aria-label="当前登录账号">
    <span class="account-avatar" aria-hidden="true">{{ initial }}</span>
    <span class="account-copy">
      <strong>{{ session.displayName }}</strong>
      <small>{{ session.account }}</small>
    </span>
    <button type="button" title="退出登录" aria-label="退出登录" @click="signOut">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5H5v14h5M14 8l4 4-4 4m4-4H9" /></svg>
    </button>
  </section>
</template>

<style scoped>
.account-menu { margin-top: auto; padding: 14px 4px 13px; display: grid; grid-template-columns: 34px minmax(0, 1fr) 34px; gap: 9px; align-items: center; border-top: 1px solid var(--line); }
.account-avatar { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 9px; color: #fff; background: var(--accent, #14736d); font-size: 13px; font-weight: 750; }
.account-copy { min-width: 0; }
.account-copy strong, .account-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-copy strong { color: var(--ink); font-size: 11px; }
.account-copy small { margin-top: 3px; color: var(--muted); font-size: 9px; }
button { width: 34px; height: 34px; padding: 0; display: grid; place-items: center; border: 0; border-radius: 8px; color: #71878a; background: transparent; cursor: pointer; }
button:hover { color: #9d4938; background: #f8e9e2; }
button:focus-visible { outline: 2px solid #14736d; outline-offset: 2px; }
svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
</style>
