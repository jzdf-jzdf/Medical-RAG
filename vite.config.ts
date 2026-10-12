import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({base:'/agent/',plugins:[vue()],server:{port:5174,proxy:{'/api/agent':{target:'http://localhost:8002',changeOrigin:true,rewrite:p=>p.replace(/^\/api\/agent/,'')}}}})
