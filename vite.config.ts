import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({base:'/knowledge/',plugins:[vue()],server:{port:5173,proxy:{'/api/knowledge':{target:'http://localhost:8001',changeOrigin:true,rewrite:p=>p.replace(/^\/api\/knowledge/,'')}}}})
