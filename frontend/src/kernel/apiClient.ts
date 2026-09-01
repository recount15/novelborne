export interface ApiClientOptions {
  baseUrl?: string
  fetcher?: typeof fetch
}

export class ApiClient {
  readonly baseUrl: string
  private readonly fetcher: typeof fetch

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl || '').replace(/\/$/, '')
    this.fetcher = options.fetcher || fetch
  }

  url(path: string): string {
    return `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`
  }

  async error(response: Response): Promise<Error> {
    try {
      const body = await response.json() as { detail?: string }
      return new Error(body.detail || `请求失败（${response.status}）`)
    } catch {
      return new Error(`请求失败（${response.status}）`)
    }
  }

  async json<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await this.fetcher(this.url(path), init)
    if (!response.ok) throw await this.error(response)
    return response.json() as Promise<T>
  }

  get<T>(path: string, init?: RequestInit): Promise<T> {
    return this.json<T>(path, init)
  }

  post<T>(path: string, body: unknown, init: RequestInit = {}): Promise<T> {
    return this.json<T>(path, {
      ...init,
      method: init.method || 'POST',
      headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
      body: JSON.stringify(body),
    })
  }

  upload<T>(path: string, form: FormData): Promise<T> {
    return this.json<T>(path, { method: 'POST', body: form })
  }

  async ndjson(path: string, body: unknown, signal: AbortSignal, onEvent: (event: unknown) => void): Promise<void> {
    const response = await this.fetcher(this.url(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    if (!response.ok) throw await this.error(response)
    if (!response.body) throw new Error('响应不支持流式读取')
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) if (line.trim()) onEvent(JSON.parse(line))
      if (done) break
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer))
  }
}

export const apiClient = new ApiClient()
