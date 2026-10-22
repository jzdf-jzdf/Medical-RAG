import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

// App.vue owns the shared shell and page bodies. These lightweight route
// records give every top-level workspace tab a stable, shareable URL.
const routes: RouteRecordRaw[] = [
  { path: '/', name: 'welcome', component: { template: '<span />' } },
  { path: '/documents', name: 'documents', component: { template: '<span />' } },
  { path: '/configurations', name: 'configurations', component: { template: '<span />' } },
  { path: '/knowledge-bases', name: 'knowledge', component: { template: '<span />' } },
  { path: '/tasks', name: 'tasks', component: { template: '<span />' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  // BASE_URL is /knowledge/ in this project. HTML5 history therefore produces
  // stable URLs such as /knowledge/tasks while the shared App shell renders the
  // corresponding page body from route.name.
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})
