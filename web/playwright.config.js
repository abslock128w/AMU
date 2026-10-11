const {defineConfig} = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests', timeout: 30000,
  use: {baseURL: 'http://127.0.0.1:8765', headless: true},
  webServer: {command: 'python3 -m http.server 8765 --bind 127.0.0.1 --directory public', url: 'http://127.0.0.1:8765', reuseExistingServer: false},
  projects: [{name: 'desktop', use: {viewport: {width: 1280, height: 800}}},
             {name: 'mobile', use: {viewport: {width: 375, height: 812}, reducedMotion: 'reduce'}}]
});
