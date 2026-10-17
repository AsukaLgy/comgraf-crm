import {defineConfig} from '@playwright/test'
import path from 'node:path'
export default defineConfig({
  testDir:'./e2e', fullyParallel:false, workers:1, timeout:30000,
  use:{baseURL:process.env.E2E_BASE_URL||'http://127.0.0.1:8001', viewport:{width:1440,height:1000}, trace:'retain-on-failure', screenshot:'only-on-failure',
    launchOptions:process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}},
  reporter:[['list'],['html',{open:'never'}]]
})
