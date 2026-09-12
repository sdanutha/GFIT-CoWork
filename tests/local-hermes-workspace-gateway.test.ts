import assert from 'node:assert/strict'
import test from 'node:test'
import { createLocalHermesWorkspaceGateway } from '../src/host/local-hermes-workspace-gateway.js'

test('uses Hermes 0.21.2 liveness and runtime-readiness surfaces', async () => {
  const originalFetch = globalThis.fetch
  const OriginalWebSocket = globalThis.WebSocket
  let livenessUrl = ''
  let runtimeUrl = ''
  let runtimeMethod = ''

  class ReadyRuntimeWebSocket extends EventTarget {
    constructor(url: string | URL) {
      super()
      runtimeUrl = String(url)
      queueMicrotask(() => this.dispatchEvent(new Event('open')))
    }

    send(data: unknown) {
      const request = JSON.parse(String(data)) as { id: string; method: string }
      runtimeMethod = request.method
      const message = new Event('message')
      Object.defineProperty(message, 'data', {
        value: JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { ok: true } }),
      })
      queueMicrotask(() => this.dispatchEvent(message))
    }

    close() {}
  }

  globalThis.fetch = async (input) => {
    livenessUrl = String(input)
    return new Response('{"ok":true}', { status: 200 })
  }
  globalThis.WebSocket = ReadyRuntimeWebSocket as unknown as typeof WebSocket

  try {
    const gateway = createLocalHermesWorkspaceGateway()
    assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
    assert.equal(livenessUrl, 'http://127.0.0.1:9119/api/health')
    assert.equal(runtimeUrl, 'ws://127.0.0.1:9119/api/ws')
    assert.equal(runtimeMethod, 'setup.runtime_check')
    await gateway.close()
  } finally {
    globalThis.fetch = originalFetch
    globalThis.WebSocket = OriginalWebSocket
  }
})

test('attaches to a healthy local Hermes server without starting another process', async () => {
  let starts = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => true,
    start: () => { starts += 1; return { stop: async () => {} } },
    waitForReady: async () => true,
  })

  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
  assert.equal(starts, 0)
})

test('rechecks runtime usability on every later health call', async () => {
  let usable = false
  let checks = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => true,
    checkUsable: async () => {
      checks += 1
      return usable
    },
    start: () => ({ stop: async () => {} }),
    waitForReady: async () => true,
  })

  assert.deepEqual(await gateway.health(), {
    kind: 'unavailable',
    remedy: 'Configure Hermes with hermes setup, then retry.',
  })
  usable = true
  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
  usable = false
  assert.deepEqual(await gateway.health(), {
    kind: 'unavailable',
    remedy: 'Configure Hermes with hermes setup, then retry.',
  })
  assert.equal(checks, 3)
})

test('observes Hermes stoppage and recovery across later health calls', async () => {
  const liveness = [true, false, true]
  let starts = 0
  let stops = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => liveness.shift() ?? false,
    checkUsable: async () => true,
    start: () => {
      starts += 1
      return { stop: async () => { stops += 1 } }
    },
    waitForReady: async () => false,
  })

  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
  assert.deepEqual(await gateway.health(), {
    kind: 'unavailable',
    remedy: 'Start Hermes with hermes serve, then retry.',
  })
  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
  assert.equal(starts, 1)
  assert.equal(stops, 1)
})

test('does not stop an externally attached Hermes server on close', async () => {
  let stops = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => true,
    start: () => ({ stop: async () => { stops += 1 } }),
    waitForReady: async () => true,
  })

  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
  await gateway.close()
  assert.equal(stops, 0)
})

test('starts Hermes once after an unavailable probe and owns only that process', async () => {
  let stops = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => false,
    start: () => ({ stop: async () => { stops += 1 } }),
    waitForReady: async () => true,
  })

  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: true })
  await gateway.close()
  assert.equal(stops, 1)
})

test('shares one startup between concurrent health checks', async () => {
  let starts = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => false,
    start: () => {
      starts += 1
      return { stop: async () => {} }
    },
    waitForReady: async () => true,
  })

  assert.deepEqual(await Promise.all([gateway.health(), gateway.health()]), [
    { kind: 'ready', startedByCoWork: true },
    { kind: 'ready', startedByCoWork: true },
  ])
  assert.equal(starts, 1)
})

test('returns a safe remedy when a started Hermes server never becomes ready', async () => {
  let stopped = false
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => false,
    start: () => ({ stop: async () => { stopped = true } }),
    waitForReady: async () => false,
  })

  assert.deepEqual(await gateway.health(), {
    kind: 'unavailable',
    remedy: 'Start Hermes with hermes serve, then retry.',
  })
  assert.equal(stopped, true)
})

test('returns the safe remedy when cleanup finds the owned process already stopped', async () => {
  const alreadyStopped = Object.assign(new Error('process already stopped'), { code: 'ESRCH' })
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => false,
    start: () => ({ stop: async () => { throw alreadyStopped } }),
    waitForReady: async () => false,
  })

  assert.deepEqual(await gateway.health(), {
    kind: 'unavailable',
    remedy: 'Start Hermes with hermes serve, then retry.',
  })
})

test('close waits for in-flight readiness, cleans ownership, and clears stale readiness', async () => {
  let resolveProbe!: (ready: boolean) => void
  let resolveStartedReadiness!: (ready: boolean) => void
  const delayedProbe = new Promise<boolean>((resolve) => { resolveProbe = resolve })
  const delayedStartedReadiness = new Promise<boolean>((resolve) => {
    resolveStartedReadiness = resolve
  })
  let probes = 0
  let starts = 0
  let stops = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => {
      probes += 1
      return probes === 1 ? delayedProbe : true
    },
    start: () => {
      starts += 1
      return { stop: async () => { stops += 1 } }
    },
    waitForReady: async () => delayedStartedReadiness,
  })

  const health = gateway.health()
  let closeSettled = false
  const close = gateway.close().then(() => { closeSettled = true })
  const queuedHealth = gateway.health()
  await Promise.resolve()
  assert.equal(closeSettled, false)
  assert.equal(probes, 1)

  resolveProbe(false)
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(starts, 1)
  resolveStartedReadiness(true)

  assert.deepEqual(await health, { kind: 'ready', startedByCoWork: true })
  await close
  assert.equal(stops, 1)
  assert.deepEqual(await queuedHealth, { kind: 'ready', startedByCoWork: false })
  assert.equal(probes, 2)
})
