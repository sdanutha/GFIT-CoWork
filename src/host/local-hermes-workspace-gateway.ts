import { spawn } from 'node:child_process'
import { stat } from 'node:fs/promises'
import { isAbsolute } from 'node:path'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'
import type {
  HermesReadiness,
  MessageRole,
  OpenThreadResult,
  OpenWorkspaceResult,
  Thread,
  ThreadMessage,
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
  readHistory?(threadId: string): Promise<ThreadMessage[]>
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
  const id = typeof row.row_id === 'string' ? row.row_id
    : typeof row.id === 'string' ? row.id : undefined
  return [{
    ...(id !== undefined ? { id } : {}),
    role: normalizedRole,
    text: messageText(row.content ?? row.text),
  }]
}

const readHistory = async (threadId: string): Promise<ThreadMessage[]> => {
  const result = await jsonRpcRequest('session.history', {
    session_id: threadId, include_row_ids: true,
  })
  const messages = (result as { messages?: unknown }).messages
  return Array.isArray(messages) ? messages.flatMap(toMessage) : []
}

const localHermesOperations: Options = {
  probe, checkUsable, start, waitForReady, statPath, listThreads, readHistory,
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
  const readThreadHistory = options.readHistory ?? readHistory

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
        // The gateway does not distinguish a deleted session from other read
        // failures, so any error folds to a safe "unreadable" recovery state.
        return { kind: 'opened', history: { threadId, messages: await readThreadHistory(threadId) } }
      } catch {
        return { kind: 'error', reason: 'unreadable' }
      }
    },
    close,
  }
}
