import { spawn } from 'node:child_process'
import { stat } from 'node:fs/promises'
import { isAbsolute } from 'node:path'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'
import type { HermesReadiness, OpenWorkspaceResult, Thread } from '../shared/contracts.js'

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
const runtimeReadinessRequestId = 'cowork-runtime-readiness'
const runtimeReadinessTimeoutMs = 10_000

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

const checkUsable = async () => {
  const token = await readSessionToken()
  if (!token) return false
  return new Promise<boolean>((resolve) => {
  let settled = false
  let socket: WebSocket | undefined
  const finish = (usable: boolean) => {
    if (settled) return
    settled = true
    clearTimeout(timeout)
    try { socket?.close() } catch {}
    resolve(usable)
  }
  const timeout = setTimeout(() => finish(false), runtimeReadinessTimeoutMs)

  try {
    socket = new WebSocket(`${hermesWebSocketUrl}?token=${encodeURIComponent(token)}`)
    socket.addEventListener('open', () => {
      try {
        socket?.send(JSON.stringify({
          jsonrpc: '2.0',
          id: runtimeReadinessRequestId,
          method: 'setup.runtime_check',
          params: {},
        }))
      } catch {
        finish(false)
      }
    })
    socket.addEventListener('message', (event) => {
      try {
        const response = JSON.parse(String(event.data)) as {
          id?: unknown
          result?: { ok?: unknown }
        }
        if (response.id === runtimeReadinessRequestId) finish(response.result?.ok === true)
      } catch {}
    })
    socket.addEventListener('error', () => finish(false))
    socket.addEventListener('close', () => finish(false))
  } catch {
    finish(false)
  }
  })
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

const toThread = (value: unknown): Thread[] => {
  if (typeof value !== 'object' || value === null) return []
  const record = value as Record<string, unknown>
  if (typeof record.id !== 'string' || typeof record.title !== 'string') return []
  return [{
    id: record.id,
    title: record.title,
    updatedAt: typeof record.updatedAt === 'string' ? record.updatedAt : '',
    activity: record.activity === 'live' ? 'live' : 'idle',
  }]
}

// Provisional: Hermes owns the Thread list for a Workspace. The exact sessions
// surface must be confirmed against a running Hermes; until then a failure
// yields an empty (not a fabricated) Thread list so the Workspace still opens.
const listThreads = async (path: string): Promise<Thread[]> => {
  try {
    const response = await fetch(`${hermesBaseUrl}/api/sessions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ workspace: path }),
    })
    if (!response.ok) return []
    const data = await response.json() as { sessions?: unknown }
    return Array.isArray(data.sessions) ? data.sessions.flatMap(toThread) : []
  } catch {
    return []
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
    close,
  }
}
