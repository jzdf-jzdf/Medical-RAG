import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({base:'/evaluation/',plugins:[vue()],server:{port:5175,proxy:{'/api/evaluation':{target:'http://localhost:8003',changeOrigin:true,rewrite:p=>p.replace(/^\/api\/evaluation/,'')}}}})
