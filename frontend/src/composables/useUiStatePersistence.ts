import { watch, onMounted, onBeforeUnmount } from 'vue'
import { saveUiState, getUiState } from '../api'

/**
 * 前端UI状态服务端持久化
 * 
 * 核心功能：
 * 1. 监听指定的 refs/reactive 对象，变化时自动保存到后端
 * 2. 组件挂载时从后端恢复状态
 * 3. 防抖优化，避免频繁请求
 * 4. 支持浏览器刷新和跨设备扫码恢复
 */

interface UiStateConfig {
  sessionId: () => string | null
  state: () => Record<string, any>
  restoreState: (restored: Record<string, any>) => void
  debounceMs?: number
  enabled?: () => boolean
}

export function useUiStatePersistence(config: UiStateConfig) {
  const { sessionId, state, restoreState, debounceMs = 1000, enabled = () => true } = config
  
  let saveTimer: ReturnType<typeof setTimeout> | null = null
  let mounted = false

  const scheduleSave = () => {
    if (!enabled() || !sessionId()) return
    
    if (saveTimer) clearTimeout(saveTimer)
    
    saveTimer = setTimeout(async () => {
      const sid = sessionId()
      if (!sid || !mounted) return
      
      try {
        const currentState = state()
        await saveUiState(sid, currentState)
      } catch (err) {
        console.warn('[UI State] 保存失败:', err)
      }
    }, debounceMs)
  }

  const restore = async () => {
    const sid = sessionId()
    if (!sid || !enabled()) return

    try {
      const response = await getUiState(sid)
      if (response.ok && response.ui_state && Object.keys(response.ui_state).length > 0) {
        restoreState(response.ui_state)
        console.log('[UI State] 已恢复状态')
      }
    } catch (err) {
      console.warn('[UI State] 恢复失败:', err)
    }
  }

  const setupWatcher = (refs: Record<string, any>) => {
    Object.entries(refs).forEach(([key, refValue]) => {
      watch(refValue, () => {
        if (mounted) scheduleSave()
      }, { deep: true })
    })
  }

  onMounted(async () => {
    mounted = true
    await restore()
  })

  onBeforeUnmount(() => {
    mounted = false
    if (saveTimer) clearTimeout(saveTimer)
    // 卸载前最后一次保存
    const sid = sessionId()
    if (sid && enabled()) {
      saveUiState(sid, state()).catch(err => {
        console.warn('[UI State] 卸载前保存失败:', err)
      })
    }
  })

  return {
    scheduleSave,
    restore,
    setupWatcher,
  }
}
