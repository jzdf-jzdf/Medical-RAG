import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles.css'
import Root from './Root.vue'
import { router } from './router'

// Register cross-page infrastructure before mounting. The current UI keeps most
// feature state in App.vue; Pinia remains installed for later module extraction.
createApp(Root).use(createPinia()).use(ElementPlus).use(router).mount('#app')
