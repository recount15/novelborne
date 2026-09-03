import { createApp } from 'vue'
import './assets/main.css'
import '@free-fonts/lxgw-wenkai-gb/lxgw-wenkai-gb.css'
import { detectPlatform } from './kernel/platform'
import AndroidShell from './shells/AndroidShell.vue'
import MobileWebShell from './shells/MobileWebShell.vue'
import WebShell from './shells/WebShell.vue'
import WindowsShell from './shells/WindowsShell.vue'

const shells = {
  web: WebShell,
  windows: WindowsShell,
  'mobile-web': MobileWebShell,
  android: AndroidShell,
}

createApp(shells[detectPlatform()]).mount('#app')
