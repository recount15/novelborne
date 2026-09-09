import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'

test('reader executes setup and renders its entry controls without initialization errors', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    server: { middlewareMode: true },
    optimizeDeps: { noDiscovery: true, include: [] },
    appType: 'custom',
  })
  try {
    const { default: Reader } = await server.ssrLoadModule('/src/components/OriginalReaderModal.vue')
    const app = createSSRApp(Reader)
    const errors = []
    app.config.errorHandler = error => { errors.push(error) }
    const html = await renderToString(app)
    assert.deepEqual(errors, [], 'setup and render must not throw (including computed/watch TDZ errors)')
    assert.match(html, /role="dialog"/)
    assert.match(html, /从本章开始/)
    assert.match(html, /与人物对话/)
    assert.match(html, /从书库打开原著/)
  } finally {
    await server.close()
  }
})
