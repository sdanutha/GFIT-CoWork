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

// session.list returns the stored session key; session.resume binds it to a live
// runtime session_id (verified against Hermes 0.21.2). Every later prompt/stop/
// stream call must use that runtime id, so remember the key -> runtime mapping.
const runtimeSessionByKey = new Map<string, string>()
const runtimeSessionId = (threadKey: string): string => runtimeSessionByKey.get(threadKey) ?? threadKey

const resumeThread = async (threadKey: string): Promise<ThreadMessage[]> => {
  const result = await jsonRpcRequest('session.resume', { session_id: threadKey }) as {
    session_id?: unknown; messages?: unknown
  }
  if (typeof result?.session_id === 'string') runtimeSessionByKey.set(threadKey, result.session_id)
  const messages = result?.messages
  return Array.isArray(messages) ? messages.flatMap(toMessage) : []
}

const createThread = async (cwd: string, title?: string): Promise<CreateThreadResult> => {
  try {
    const result = await jsonRpcRequest('session.create', {
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
  await jsonRpcRequest('prompt.submit', { session_id: runtimeSessionId(threadId), text })
}

const stopThread = async (threadId: string): Promise<void> => {
  await jsonRpcRequest('session.interrupt', { session_id: runtimeSessionId(threadId) })
}

const respondApproval = async (
  threadId: string,
  requestId: string,
  choice: ApprovalChoice,
): Promise<void> => {
  await jsonRpcRequest('approval.respond', {
    session_id: runtimeSessionId(threadId), request_id: requestId, choice,
  })
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

const mapStreamEvent = (raw: unknown, threadId: string): ThreadStreamEvent | undefined => {
  let frame: { method?: unknown; params?: unknown }
  try {
    frame = JSON.parse(String(raw)) as { method?: unknown; params?: unknown }
  } catch {
    return undefined
  }
  if (typeof frame.method !== 'string') return undefined
  const params = (typeof frame.params === 'object' && frame.params !== null
    ? frame.params : {}) as Record<string, unknown>
  // Accept the stored key or its bound runtime session id (see runtimeSessionByKey).
  if (typeof params.session_id === 'string'
    && params.session_id !== threadId
    && params.session_id !== runtimeSessionByKey.get(threadId)) return undefined
  switch (frame.method) {
    case 'turn.start': case 'turn.started': return { kind: 'turn-start' }
    case 'turn.end': return { kind: 'turn-end' }
    case 'turn.error': return { kind: 'turn-error', message: 'The Hermes turn ended with an error.' }
    case 'message.start': return { kind: 'message-start', role: streamRole(params.role) }
    case 'message.delta':
      return { kind: 'message-delta', text: typeof params.text === 'string' ? params.text : '' }
    case 'message.complete': return { kind: 'message-complete' }
    case 'approval.request':
      return typeof params.request_id === 'string'
        ? { kind: 'approval-request', requestId: params.request_id, action: approvalAction(params) }
        : undefined
    case 'approval.resolved':
      return typeof params.request_id === 'string'
        ? { kind: 'approval-resolved', requestId: params.request_id, decision: approvalDecision(params) }
        : undefined
    default: return undefined
  }
}

// Provisional (best-effort, see docs/agents/hermes-api.md): opens a gateway
// socket and forwards this thread's turn notifications. Whether Hermes pushes a
// session's events to a fresh socket depends on attachment semantics not yet
// verified against a live turn; tool-activity events are exercised through tests
// until the live surface is confirmed.
const subscribe = (
  threadId: string,
  listener: (event: ThreadStreamEvent) => void,
): (() => void) => {
  let socket: WebSocket | undefined
  let closed = false
  void readSessionToken().then((token) => {
    if (closed || !token) return
    try {
      socket = new WebSocket(`${hermesWebSocketUrl}?token=${encodeURIComponent(token)}`)
      socket.addEventListener('message', (event) => {
        const mapped = mapStreamEvent(event.data, threadId)
        if (mapped) listener(mapped)
      })
    } catch {
      // A failed subscription simply yields no events; the turn still runs in Hermes.
    }
  }, () => {})
  return () => { closed = true; try { socket?.close() } catch {} }
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
