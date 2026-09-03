export type PlatformKind = 'web' | 'windows' | 'mobile-web' | 'android'

type WindowBridge = {
  minimize?: () => void
  toggle_maximize?: () => void
  close?: () => void
}

type PywebviewWindow = { pywebview?: { api?: WindowBridge } }

export interface PlatformAdapter {
  kind: PlatformKind
  isDesktop: boolean
  isTouch: boolean
  apiBaseUrl: string
  storage: Storage | null
  openExternal(url: string): void
  closeWindow(): void
  minimizeWindow(): void
  toggleMaximize(): void
}

function safeStorage(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function openExternal(url: string): void {
  const opened = window.open(url, '_blank', 'noopener,noreferrer')
  if (!opened) window.location.assign(url)
}

function hasPywebview(): boolean {
  return typeof window !== 'undefined' && 'pywebview' in window
}

export function detectPlatform(): PlatformKind {
  if (hasPywebview()) return 'windows'
  const configured = import.meta.env.VITE_PLATFORM as PlatformKind | undefined
  if (configured === 'android' || configured === 'mobile-web' || configured === 'web' || configured === 'windows') return configured
  return window.matchMedia?.('(pointer: coarse)').matches ? 'mobile-web' : 'web'
}

export function createPlatformAdapter(kind: PlatformKind = detectPlatform()): PlatformAdapter {
  const bridge = () => (window as unknown as PywebviewWindow).pywebview?.api
  return {
    kind,
    isDesktop: kind === 'web' || kind === 'windows',
    isTouch: kind === 'mobile-web' || kind === 'android',
    apiBaseUrl: (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') || '',
    storage: safeStorage(),
    openExternal,
    closeWindow: () => bridge()?.close?.(),
    minimizeWindow: () => bridge()?.minimize?.(),
    toggleMaximize: () => bridge()?.toggle_maximize?.(),
  }
}

export const platform = createPlatformAdapter()
