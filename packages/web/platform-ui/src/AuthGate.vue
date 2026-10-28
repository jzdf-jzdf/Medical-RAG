<script setup lang="ts">
import LoginView from './LoginView.vue'
import { session } from './auth'
</script>

<template>
  <slot v-if="session" />
  <LoginView v-else />
</template>
