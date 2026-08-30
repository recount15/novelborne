import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'com.novelborne.app',
  appName: '书中织梦',
  webDir: 'dist',
  android: {
    allowMixedContent: process.env.CAP_ALLOW_CLEARTEXT === '1',
  },
}

export default config
