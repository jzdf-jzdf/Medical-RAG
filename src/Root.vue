<script setup lang="ts">
import App from './App.vue'
import LoginView from './components/LoginView.vue'
import { session } from './auth'
</script>

<template>
  <LoginView v-if="!session" />
  <App v-else />
</template>
