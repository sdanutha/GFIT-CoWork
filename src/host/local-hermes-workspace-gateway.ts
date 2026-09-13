import { spawn } from 'node:child_process'
import { stat } from 'node:fs/promises'
import { isAbsolute } from 'node:path'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'
import type {
  ApprovalChoice,
  ApprovalDecision,
  CreateThreadResult,
  HermesReadiness,
  MessageRole,
  OpenThreadResult,
  OpenWorkspaceResult,
  Thread,
  ThreadMessage,
  ThreadStreamEvent,
} from '../shared/contracts.js'

type OwnedProcess = {
  stop(): Promise<void>
  onExit?(listener: () => void): void
}
type PathStatus = 'directory' | 'not-found' | 'not-a-directory' | 'unreadable'
type Options = {
  probe(): Promise<boolean>
  checkUsable?(): Promise<boolean>
  start(): OwnedProcess
  waitForReady(): Promise<boolean>
  statPath?(path: string): Promise<PathStatus>
  listThreads?(path: string): Promise<Thread[]>
}

const hermesBaseUrl = 'http://127.0.0.1:9119'
const hermesWebSocketUrl = 'ws://127.0.0.1:9119/api/ws'
const gatewayRequestTimeoutMs = 10_000

const probe = async () => {
  try { return (await fetch(`${hermesBaseUrl}/api/health`)).ok } catch { return false }
}

const readSessionToken = async (): Promise<string | undefined> => {
  try {
    const response = await fetch(`${hermesBaseUrl}/`)
    if (!response.ok) return undefined
    const html = await response.text()
    const match = html.match(/window\.__HERMES_SESSION_TOKEN__\s*=\s*("(?:\\.|[^"\\])*")\s*;/)
    if (!match) return undefined
    const token = JSON.parse(match[1])
    return typeof token === 'string' && token.length > 0 ? token : undefined
  } catch {
    return undefined
  }
}

// One JSON-RPC request/response over an authenticated gateway WebSocket. Opens a
// socket, sends the method, resolves the first matching response `result`, and
// closes; rejects on error, close, timeout, or a missing session token.
const jsonRpcRequest = (
  method: string,
  params: Record<string, unknown> = {},
  timeoutMs = gatewayRequestTimeoutMs,
): Promise<unknown> => new Promise((resolve, reject) => {
  let settled = false
  let socket: WebSocket | undefined
  const requestId = `cowork-${method}`
  const finish = (complete: () => void) => {
    if (settled) return
    settled = true
    clearTimeout(timeout)
    try { socket?.close() } catch {}
    complete()
  }
  const timeout = setTimeout(() => finish(() => reject(new Error('gateway request timed out'))), timeoutMs)

  readSessionToken().then((token) => {
    if (settled) return
    if (!token) return finish(() => reject(new Error('no session token')))
    try {
      socket = new WebSocket(`${hermesWebSocketUrl}?token=${encodeURIComponent(token)}`)
      socket.addEventListener('open', () => {
        try {
          socket?.send(JSON.stringify({ jsonrpc: '2.0', id: requestId, method, params }))
        } catch (error) {
          finish(() => reject(error))
        }
      })
      socket.addEventListener('message', (event) => {
        try {
          const response = JSON.parse(String(event.data)) as {
            id?: unknown; result?: unknown; error?: unknown
          }
          if (response.id !== requestId) return
          if (response.error !== undefined) finish(() => reject(new Error('gateway returned an error')))
          else finish(() => resolve(response.result))
        } catch {}
      })
      socket.addEventListener('error', () => finish(() => reject(new Error('gateway socket error'))))
      socket.addEventListener('close', () => finish(() => reject(new Error('gateway socket closed'))))
    } catch (error) {
      finish(() => reject(error))
    }
  }, () => finish(() => reject(new Error('no session token'))))
})

const checkUsable = async () => {
  try {
    const result = await jsonRpcRequest('setup.runtime_check') as { ok?: unknown }
    return result?.ok === true
  } catch {
    return false
  }
}

const start = () => {
  const child = spawn('hermes', ['serve', '--host', '127.0.0.1', '--port', '9119'], {
    detached: process.platform !== 'win32', stdio: 'ignore', shell: false,
  })
  return {
    onExit: (listener: () => void) => {
      child.once('error', listener)
      child.once('exit', listener)
    },
    stop: async () => { if (child.pid) process.kill(child.pid, 'SIGTERM') },
  }
}

const waitForReady = async () => {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (await probe()) return true
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  return false
}

const errorCode = (error: unknown): string | undefined => (
  typeof error === 'object' && error !== null && 'code' in error
    ? String((error as { code: unknown }).code)
    : undefined
)

const statPath = async (path: string): Promise<PathStatus> => {
  try {
    return (await stat(path)).isDirectory() ? 'directory' : 'not-a-directory'
  } catch (error) {
    if (errorCode(error) === 'ENOENT') return 'not-found'
    return 'unreadable'
  }
}

// Hermes stamps session.list `started_at` as epoch seconds.
const toIsoRecency = (startedAt: unknown): string => {
  if (typeof startedAt !== 'number' || startedAt <= 0) return ''
  const date = new Date(startedAt * 1000)
  return Number.isNaN(date.getTime()) ? '' : date.toISOString()
}

const toThread = (value: unknown, liveIds: Set<string>): Thread[] => {
  if (typeof value !== 'object' || value === null) return []
  const row = value as Record<string, unknown>
  if (typeof row.id !== 'string') return []
  return [{
    id: row.id,
    title: typeof row.title === 'string' && row.title.length > 0 ? row.title : 'Untitled',
    updatedAt: toIsoRecency(row.started_at),
    activity: liveIds.has(row.id) ? 'live' : 'idle',
  }]
}

const liveSessionIds = (value: unknown): Set<string> => {
  const ids = new Set<string>()
  const rows = typeof value === 'object' && value !== null
    ? (value as { sessions?: unknown }).sessions
    : undefined
  if (Array.isArray(rows)) {
    for (const row of rows) {
      if (typeof row !== 'object' || row === null) continue
      const record = row as Record<string, unknown>
      for (const key of ['id', 'session_id', 'stored_session_id'] as const) {
        if (typeof record[key] === 'string') ids.add(record[key] as string)
      }
    }
  }
  return ids
}

// Best-effort (ADR 0002): the gateway lists recent sessions with no folder
// filter and no cwd, so the Workspace path cannot narrow the list here. Activity
// is enriched from the live-session list where ids line up. A failure yields an
// empty (not a fabricated) list so the Workspace still opens.
const listThreads = async (_workspacePath: string): Promise<Thread[]> => {
  try {
    const [listed, active] = await Promise.all([
      jsonRpcRequest('session.list', { limit: 200 }),
      jsonRpcRequest('session.active_list').catch(() => ({ sessions: [] })),
    ])
    const liveIds = liveSessionIds(active)
    const rows = (listed as { sessions?: unknown }).sessions
    return Array.isArray(rows) ? rows.flatMap((row) => toThread(row, liveIds)) : []
  } catch {
    return []
  }
}

const messageText = (content: unknown): string => {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((block) => (typeof block === 'object' && block !== null
        && typeof (block as { text?: unknown }).text === 'string'
        ? (block as { text: string }).text
        : ''))
      .join('')
  }
  return ''
}

const toMessage = (value: unknown): ThreadMessage[] => {
  if (typeof value !== 'object' || value === null) return []
  const row = value as Record<string, unknown>
  const role = row.role
  const normalizedRole: MessageRole =
    role === 'user' || role === 'assistant' || role === 'tool' ? role : 'system'
  const rawId = row.row_id ?? row.id
  const id = typeof rawId === 'string' ? rawId
    : typeof rawId === 'number' ? String(rawId) : undefined
  return [{
    ...(id !== undefined ? { id } : {}),
    role: normalizedRole,
    text: messageText(row.text ?? row.content),
  }]
}

const approvalAction = (params: Record<string, unknown>): string => {
  for (const key of ['action', 'summary', 'command', 'title'] as const) {
    if (typeof params[key] === 'string' && (params[key] as string).length > 0) return params[key] as string
  }
  return 'Hermes requested approval for an action.'
}

const approvalDecision = (params: Record<string, unknown>): ApprovalDecision => {
  const raw = String(params.decision ?? params.status ?? params.choice ?? '').toLowerCase()
  if (raw.includes('allow') || raw.includes('approve')) return 'allowed'
  if (raw.includes('deny') || raw.includes('reject')) return 'denied'
  if (raw.includes('expire')) return 'expired'
  return 'failed'
}

const streamRole = (role: unknown): MessageRole =>
  role === 'user' || role === 'assistant' || role === 'tool' ? role : 'assistant'

// tool.start/tool.complete payloads (verified): { name, context (the command),
// args, result: { output, exit_code, error } }.
const toolSummary = (payload: Record<string, unknown>): string => {
  // ThreadView already prefixes the tool name, so summarize with the command only.
  const context = typeof payload.context === 'string' ? payload.context.trim() : ''
  return context.length > 0 ? context : 'completed'
}

const toolDetails = (payload: Record<string, unknown>): string | undefined => {
  const result = typeof payload.result === 'object' && payload.result !== null
    ? payload.result as Record<string, unknown> : {}
  const output = typeof result.output === 'string' ? result.output : ''
  const error = typeof result.error === 'string' ? result.error : ''
  const details = [output, error].filter((part) => part.length > 0).join('\n')
  return details.length > 0 ? details : undefined
}

type GatewayEvent = { type: string; sessionId: string; payload: Record<string, unknown> }

// Verified live (Hermes 0.21.2): streaming updates arrive as method:"event" with
// the specifics in params; there is no dedicated turn.* event — session.info's
// `running` flag is the turn boundary. Text lives in payload.text.
const mapStreamEvent = (event: GatewayEvent): ThreadStreamEvent | undefined => {
  const { type, payload } = event
  switch (type) {
    case 'session.info':
      return payload.running === true ? { kind: 'turn-start' }
        : payload.running === false ? { kind: 'turn-end' }
          : undefined
    case 'turn.start': case 'turn.started': return { kind: 'turn-start' }
    case 'turn.end': return { kind: 'turn-end' }
    case 'turn.error': return { kind: 'turn-error', message: 'The Hermes turn ended with an error.' }
    case 'message.start': return { kind: 'message-start', role: streamRole(payload.role) }
    case 'message.delta':
      return { kind: 'message-delta', text: typeof payload.text === 'string' ? payload.text : '' }
    case 'message.complete': return { kind: 'message-complete' }
    case 'tool.start':
      return typeof payload.name === 'string'
        ? { kind: 'tool-start', tool: payload.name }
        : undefined
    case 'tool.complete':
      return typeof payload.name === 'string'
        ? { kind: 'tool-end', tool: payload.name, summary: toolSummary(payload), details: toolDetails(payload) }
        : undefined
    case 'approval.request':
      return typeof payload.request_id === 'string'
        ? { kind: 'approval-request', requestId: payload.request_id, action: approvalAction(payload) }
        : undefined
    case 'approval.expire':
      return typeof payload.request_id === 'string'
        ? { kind: 'approval-resolved', requestId: payload.request_id, decision: 'expired' }
        : undefined
    case 'approval.resolved':
      return typeof payload.request_id === 'string'
        ? { kind: 'approval-resolved', requestId: payload.request_id, decision: approvalDecision(payload) }
        : undefined
    default: return undefined
  }
}

type PendingRequest = {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

export type GatewayConnection = {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>
  onEvent(listener: (event: GatewayEvent) => void): () => void
  close(): void
}

// One authenticated gateway WebSocket, shared by every stateful call for a
// session. Hermes binds a session to the connection that created or resumed it,
// so create -> prompt -> stream -> stop MUST share one socket (verified live);
// request/response correlate by id and `event` notifications fan out to listeners.
export function createGatewayConnection(): GatewayConnection {
  let socket: WebSocket | undefined
  let connecting: Promise<WebSocket> | undefined
  let counter = 0
  const pending = new Map<string, PendingRequest>()
  const listeners = new Set<(event: GatewayEvent) => void>()

  const failAll = (reason: string) => {
    for (const [, request] of pending) { clearTimeout(request.timer); request.reject(new Error(reason)) }
    pending.clear()
  }

  const handle = (data: unknown) => {
    let frame: { id?: unknown; result?: unknown; error?: unknown; method?: unknown; params?: unknown }
    try {
      frame = JSON.parse(String(data)) as typeof frame
    } catch {
      return
    }
    if (typeof frame.id === 'string' && pending.has(frame.id)) {
      const request = pending.get(frame.id)!
      pending.delete(frame.id)
      clearTimeout(request.timer)
      if (frame.error !== undefined) request.reject(new Error('gateway returned an error'))
      else request.resolve(frame.result)
      return
    }
    if (frame.method === 'event' && typeof frame.params === 'object' && frame.params !== null) {
      const params = frame.params as Record<string, unknown>
      if (typeof params.type !== 'string') return
      const event: GatewayEvent = {
        type: params.type,
        sessionId: typeof params.session_id === 'string' ? params.session_id : '',
        payload: typeof params.payload === 'object' && params.payload !== null
          ? params.payload as Record<string, unknown> : {},
      }
      for (const listener of listeners) listener(event)
    }
  }

  const connect = (): Promise<WebSocket> => {
    if (socket && socket.readyState === 1) return Promise.resolve(socket)
    if (connecting) return connecting
    connecting = (async () => {
      const token = await readSessionToken()
      if (!token) { connecting = undefined; throw new Error('no session token') }
      return await new Promise<WebSocket>((resolve, reject) => {
        let opened = false
        const ws = new WebSocket(`${hermesWebSocketUrl}?token=${encodeURIComponent(token)}`)
        ws.addEventListener('open', () => { opened = true; socket = ws; connecting = undefined; resolve(ws) })
        ws.addEventListener('message', (event) => handle(event.data))
        ws.addEventListener('close', () => {
          if (socket === ws) socket = undefined
          connecting = undefined
          failAll('gateway socket closed')
        })
        ws.addEventListener('error', () => {
          connecting = undefined
          failAll('gateway socket error')
          if (!opened) reject(new Error('gateway socket error'))
          try { ws.close() } catch { /* already closing */ }
        })
      })
    })()
    return connecting
  }

  return {
    async request(method, params = {}, timeoutMs = gatewayRequestTimeoutMs) {
      const ws = await connect()
      counter += 1
      const id = `cowork-${method}-${counter}`
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id)
          reject(new Error('gateway request timed out'))
        }, timeoutMs)
        pending.set(id, { resolve, reject, timer })
        try {
          ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
        } catch (error) {
          pending.delete(id)
          clearTimeout(timer)
          reject(error instanceof Error ? error : new Error('gateway send failed'))
        }
      })
    },
    onEvent(listener) {
      listeners.add(listener)
      void connect().catch(() => { /* events resume when a later request reconnects */ })
      return () => { listeners.delete(listener) }
    },
    close() {
      try { socket?.close() } catch { /* already closing */ }
      socket = undefined
      failAll('gateway connection closed')
    },
  }
}

const localHermesOperations: Options = {
  probe, checkUsable, start, waitForReady, statPath, listThreads,
}

const isAlreadyStoppedError = (error: unknown) => (
  typeof error === 'object' && error !== null && 'code' in error && error.code === 'ESRCH'
)

export function createLocalHermesWorkspaceGateway(
  options: Options = localHermesOperations,
): HermesWorkspaceGateway {
  let owned: OwnedProcess | undefined
  let inFlight: Promise<HermesReadiness> | undefined
  let closing: Promise<void> | undefined

  // One shared gateway connection for this workspace's stateful session calls.
  const connection = createGatewayConnection()
  // session.list yields a stored key; session.resume binds it to a runtime
  // session_id that every later prompt/stop/stream/approval call must use.
  const runtimeSessionByKey = new Map<string, string>()
  const runtimeSessionId = (threadKey: string): string => runtimeSessionByKey.get(threadKey) ?? threadKey

  const stopOwned = async () => {
    const ownedProcess = owned
    owned = undefined
    if (!ownedProcess) return
    try {
      await ownedProcess.stop()
    } catch (error) {
      if (!isAlreadyStoppedError(error)) throw error
    }
  }

  const resolveReadiness = async (): Promise<HermesReadiness> => {
    const isUsable = options.checkUsable ?? (async () => true)
    if (await options.probe()) {
      return await isUsable()
        ? { kind: 'ready', startedByCoWork: owned !== undefined }
        : { kind: 'unavailable', remedy: 'Configure Hermes with hermes setup, then retry.' }
    }
    await stopOwned()
    const started = options.start()
    owned = started
    started.onExit?.(() => {
      if (owned === started) owned = undefined
    })
    if (await options.waitForReady()) {
      return await isUsable()
        ? { kind: 'ready', startedByCoWork: true }
        : { kind: 'unavailable', remedy: 'Configure Hermes with hermes setup, then retry.' }
    }
    await stopOwned()
    return { kind: 'unavailable', remedy: 'Start Hermes with hermes serve, then retry.' }
  }

  const close = () => {
    if (closing) return closing
    const activeReadiness = inFlight
    const closeOperation = (async () => {
      try {
        await activeReadiness
      } finally {
        try {
          connection.close()
          await stopOwned()
        } finally {
          if (inFlight === activeReadiness) inFlight = undefined
        }
      }
    })()
    closing = closeOperation.finally(() => { closing = undefined })
    return closing
  }

  const validatePath = options.statPath ?? statPath
  const readThreads = options.listThreads ?? listThreads

  const resumeThread = async (threadKey: string): Promise<ThreadMessage[]> => {
    const result = await connection.request('session.resume', { session_id: threadKey }) as {
      session_id?: unknown; messages?: unknown
    }
    if (typeof result?.session_id === 'string') runtimeSessionByKey.set(threadKey, result.session_id)
    const messages = result?.messages
    return Array.isArray(messages) ? messages.flatMap(toMessage) : []
  }

  const createThread = async (cwd: string, title?: string): Promise<CreateThreadResult> => {
    try {
      const result = await connection.request('session.create', {
        ...(cwd.length > 0 ? { cwd } : {}),
        ...(title ? { title } : {}),
        source: 'gfit-cowork',
      }) as { session_id?: unknown }
      return typeof result?.session_id === 'string'
        ? { kind: 'created', threadId: result.session_id }
        : { kind: 'error' }
    } catch {
      return { kind: 'error' }
    }
  }

  const submitPrompt = async (threadId: string, text: string): Promise<void> => {
    await connection.request('prompt.submit', { session_id: runtimeSessionId(threadId), text })
  }

  const stopThread = async (threadId: string): Promise<void> => {
    await connection.request('session.interrupt', { session_id: runtimeSessionId(threadId) })
  }

  const respondApproval = async (
    threadId: string,
    requestId: string,
    choice: ApprovalChoice,
  ): Promise<void> => {
    await connection.request('approval.respond', {
      session_id: runtimeSessionId(threadId), request_id: requestId, choice,
    })
  }

  const subscribe = (
    threadId: string,
    listener: (event: ThreadStreamEvent) => void,
  ): (() => void) => connection.onEvent((event) => {
    // Accept events for the stored key or its bound runtime session id.
    const runtime = runtimeSessionByKey.get(threadId)
    if (event.sessionId && event.sessionId !== threadId && event.sessionId !== runtime) return
    const mapped = mapStreamEvent(event)
    if (mapped) listener(mapped)
  })

  return {
    async health() {
      if (closing) await closing
      if (inFlight) return inFlight
      const operation = resolveReadiness()
      inFlight = operation
      try {
        return await operation
      } finally {
        if (inFlight === operation) inFlight = undefined
      }
    },
    async openWorkspace(path: string): Promise<OpenWorkspaceResult> {
      if (!isAbsolute(path)) return { kind: 'error', reason: 'not-absolute' }
      const status = await validatePath(path)
      if (status !== 'directory') return { kind: 'error', reason: status }
      return { kind: 'opened', workspace: { path, threads: await readThreads(path) } }
    },
    async openThread(threadId: string): Promise<OpenThreadResult> {
      try {
        // session.resume binds the stored key to a runtime session and returns
        // its history. The gateway does not distinguish a deleted session from
        // other read failures, so any error folds to a safe "unreadable" state.
        return { kind: 'opened', history: { threadId, messages: await resumeThread(threadId) } }
      } catch {
        return { kind: 'error', reason: 'unreadable' }
      }
    },
    createThread,
    submitPrompt,
    stopThread,
    respondApproval,
    subscribe,
    close,
  }
}
