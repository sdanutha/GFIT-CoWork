import { spawn } from 'node:child_process'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'
import type { HermesReadiness } from '../shared/contracts.js'

type OwnedProcess = { stop(): Promise<void> }
type Options = {
  probe(): Promise<boolean>
  start(): OwnedProcess
  waitForReady(): Promise<boolean>
}

const hermesBaseUrl = 'http://127.0.0.1:9119'

const probe = async () => {
  try { return (await fetch(`${hermesBaseUrl}/health`)).ok } catch { return false }
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

const localHermesOperations: Options = { probe, start, waitForReady }

export function createLocalHermesWorkspaceGateway(
  options: Options = localHermesOperations,
): HermesWorkspaceGateway {
  let owned: OwnedProcess | undefined
  let readiness: HermesReadiness | undefined

  return {
    async health() {
      if (readiness) return readiness
      if (await options.probe()) return (readiness = { kind: 'ready', startedByCoWork: false })
      owned = options.start()
      if (await options.waitForReady()) return (readiness = { kind: 'ready', startedByCoWork: true })
      await owned.stop()
      owned = undefined
      return (readiness = { kind: 'unavailable', remedy: 'Start Hermes with hermes serve, then retry.' })
    },
    async close() {
      await owned?.stop()
      owned = undefined
      readiness = undefined
    },
  }
}
