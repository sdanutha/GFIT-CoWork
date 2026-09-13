import { spawn } from 'node:child_process'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'
import type { HermesReadiness } from '../shared/contracts.js'

type OwnedProcess = { stop(): Promise<void> }
type Options = {
  probe(): Promise<boolean>
  checkUsable?(): Promise<boolean>
  start(): OwnedProcess
  waitForReady(): Promise<boolean>
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
  child.on('error', () => {})
  return { stop: async () => { if (child.pid) process.kill(child.pid, 'SIGTERM') } }
}

const waitForReady = async () => {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (await probe()) return true
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  return false
}

const localHermesOperations: Options = { probe, checkUsable, start, waitForReady }

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
    owned = options.start()
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
    close,
  }
}
