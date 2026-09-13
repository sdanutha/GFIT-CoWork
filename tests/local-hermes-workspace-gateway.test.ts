import assert from 'node:assert/strict'
import test from 'node:test'
import { createLocalHermesWorkspaceGateway } from '../src/host/local-hermes-workspace-gateway.js'

test('uses Hermes 0.21.2 liveness and runtime-readiness surfaces', async () => {
  const originalFetch = globalThis.fetch
  const OriginalWebSocket = globalThis.WebSocket
  let livenessUrl = ''
  let tokenUrl = ''
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
    const url = String(input)
    if (url.endsWith('/api/health')) {
      livenessUrl = url
    } else {
      tokenUrl = url
    }
    if (url.endsWith('/')) {
      return new Response(
        '<script>window.__HERMES_SESSION_TOKEN__="ephemeral-loopback-token";</script>',
        { status: 200 },
      )
    }
    return new Response('{"ok":true}', { status: 200 })
  }
  globalThis.WebSocket = ReadyRuntimeWebSocket as unknown as typeof WebSocket

  try {
    const gateway = createLocalHermesWorkspaceGateway()
    assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
    assert.equal(livenessUrl, 'http://127.0.0.1:9119/api/health')
    assert.equal(tokenUrl, 'http://127.0.0.1:9119/')
    assert.equal(runtimeUrl, 'ws://127.0.0.1:9119/api/ws?token=ephemeral-loopback-token')
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

test('clears ownership when the started Hermes child exits before a later external attach', async () => {
  let childExited!: () => void
  let stops = 0
  let starts = 0
  const gateway = createLocalHermesWorkspaceGateway({
    probe: async () => starts === 0 ? false : true,
    start: () => {
      starts += 1
      return {
        onExit: (listener: () => void) => { childExited = listener },
        stop: async () => { stops += 1 },
      }
    },
    waitForReady: async () => true,
  })

  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: true })
  childExited()
  assert.deepEqual(await gateway.health(), { kind: 'ready', startedByCoWork: false })
  await gateway.close()
  assert.equal(stops, 0)
})

// openWorkspace never touches the health operations, so keep them inert here.
const baseHealthOps = {
  probe: async () => true,
  start: () => ({ stop: async () => {} }),
  waitForReady: async () => true,
}

test('rejects a relative Workspace path without touching the filesystem', async () => {
  let statted = false
  const gateway = createLocalHermesWorkspaceGateway({
    ...baseHealthOps,
    statPath: async () => { statted = true; return 'directory' },
  })

  assert.deepEqual(await gateway.openWorkspace('relative/path'), {
    kind: 'error',
    reason: 'not-absolute',
  })
  assert.equal(statted, false)
})

test('maps filesystem status of an absolute path to a validation reason', async () => {
  for (const status of ['not-found', 'not-a-directory', 'unreadable'] as const) {
    const gateway = createLocalHermesWorkspaceGateway({ ...baseHealthOps, statPath: async () => status })
    assert.deepEqual(await gateway.openWorkspace('/home/dev/x'), {
      kind: 'error',
      reason: status,
    })
  }
})

test('opens an absolute directory and returns its Hermes Threads scoped to the path', async () => {
  const requestedPaths: string[] = []
  const gateway = createLocalHermesWorkspaceGateway({
    ...baseHealthOps,
    statPath: async () => 'directory',
    listThreads: async (path) => {
      requestedPaths.push(path)
      return [{ id: 't1', title: 'Scoped', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' }]
    },
  })

  assert.deepEqual(await gateway.openWorkspace('/home/dev/project'), {
    kind: 'opened',
    workspace: {
      path: '/home/dev/project',
      threads: [{ id: 't1', title: 'Scoped', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' }],
    },
  })
  assert.deepEqual(requestedPaths, ['/home/dev/project'])
})

test('lists Threads from session.list, enriching activity from the live session list', async () => {
  const originalFetch = globalThis.fetch
  const OriginalWebSocket = globalThis.WebSocket

  class GatewayWebSocket extends EventTarget {
    constructor(url: string | URL) {
      super()
      void url
      queueMicrotask(() => this.dispatchEvent(new Event('open')))
    }

    send(data: unknown) {
      const request = JSON.parse(String(data)) as { id: string; method: string }
      const result =
        request.method === 'session.list'
          ? { sessions: [
              { id: 's1', title: 'First thread', started_at: 1_700_000_000 },
              { id: 's2', title: '', started_at: 0 },
            ] }
          : request.method === 'session.active_list'
            ? { sessions: [{ id: 's2' }] }
            : {}
      const message = new Event('message')
      Object.defineProperty(message, 'data', {
        value: JSON.stringify({ jsonrpc: '2.0', id: request.id, result }),
      })
      queueMicrotask(() => this.dispatchEvent(message))
    }

    close() {}
  }

  globalThis.fetch = async (input) => {
    const url = String(input)
    if (url.endsWith('/')) {
      return new Response(
        '<script>window.__HERMES_SESSION_TOKEN__="ephemeral-loopback-token";</script>',
        { status: 200 },
      )
    }
    return new Response('{"ok":true}', { status: 200 })
  }
  globalThis.WebSocket = GatewayWebSocket as unknown as typeof WebSocket

  try {
    const gateway = createLocalHermesWorkspaceGateway({
      ...baseHealthOps,
      statPath: async () => 'directory',
    })
    const result = await gateway.openWorkspace('/home/dev/project')

    assert.deepEqual(result, {
      kind: 'opened',
      workspace: {
        path: '/home/dev/project',
        threads: [
          {
            id: 's1',
            title: 'First thread',
            updatedAt: new Date(1_700_000_000 * 1000).toISOString(),
            activity: 'idle',
          },
          { id: 's2', title: 'Untitled', updatedAt: '', activity: 'live' },
        ],
      },
    })
  } finally {
    globalThis.fetch = originalFetch
    globalThis.WebSocket = OriginalWebSocket
  }
})

test('opens a Thread by mapping session.history into ordered messages', async () => {
  const originalFetch = globalThis.fetch
  const OriginalWebSocket = globalThis.WebSocket
  let historyMethod = ''
  let historyParams: Record<string, unknown> = {}

  class HistoryWebSocket extends EventTarget {
    constructor(url: string | URL) { super(); void url; queueMicrotask(() => this.dispatchEvent(new Event('open'))) }
    send(data: unknown) {
      const request = JSON.parse(String(data)) as { id: string; method: string; params: Record<string, unknown> }
      historyMethod = request.method
      historyParams = request.params
      const message = new Event('message')
      Object.defineProperty(message, 'data', {
        value: JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { count: 3, messages: [
          { row_id: 'r1', role: 'user', content: 'hi' },
          { role: 'assistant', content: [{ text: 'hel' }, { text: 'lo' }] },
          { role: 'tool', content: 'ran tests' },
        ] } }),
      })
      queueMicrotask(() => this.dispatchEvent(message))
    }
    close() {}
  }

  globalThis.fetch = async (input) => String(input).endsWith('/')
    ? new Response('<script>window.__HERMES_SESSION_TOKEN__="ephemeral-loopback-token";</script>', { status: 200 })
    : new Response('{"ok":true}', { status: 200 })
  globalThis.WebSocket = HistoryWebSocket as unknown as typeof WebSocket

  try {
    const gateway = createLocalHermesWorkspaceGateway(baseHealthOps)
    assert.deepEqual(await gateway.openThread('s1'), {
      kind: 'opened',
      history: {
        threadId: 's1',
        messages: [
          { id: 'r1', role: 'user', text: 'hi' },
          { role: 'assistant', text: 'hello' },
          { role: 'tool', text: 'ran tests' },
        ],
      },
    })
    assert.equal(historyMethod, 'session.history')
    assert.equal(historyParams.session_id, 's1')
  } finally {
    globalThis.fetch = originalFetch
    globalThis.WebSocket = OriginalWebSocket
  }
})

test('folds a gateway history error into an unreadable Thread state', async () => {
  const originalFetch = globalThis.fetch
  const OriginalWebSocket = globalThis.WebSocket

  class ErrorWebSocket extends EventTarget {
    constructor(url: string | URL) { super(); void url; queueMicrotask(() => this.dispatchEvent(new Event('open'))) }
    send(data: unknown) {
      const request = JSON.parse(String(data)) as { id: string }
      const message = new Event('message')
      Object.defineProperty(message, 'data', {
        value: JSON.stringify({ jsonrpc: '2.0', id: request.id, error: { code: 5001, message: 'nope' } }),
      })
      queueMicrotask(() => this.dispatchEvent(message))
    }
    close() {}
  }

  globalThis.fetch = async (input) => String(input).endsWith('/')
    ? new Response('<script>window.__HERMES_SESSION_TOKEN__="ephemeral-loopback-token";</script>', { status: 200 })
    : new Response('{"ok":true}', { status: 200 })
  globalThis.WebSocket = ErrorWebSocket as unknown as typeof WebSocket

  try {
    const gateway = createLocalHermesWorkspaceGateway(baseHealthOps)
    assert.deepEqual(await gateway.openThread('s1'), { kind: 'error', reason: 'unreadable' })
  } finally {
    globalThis.fetch = originalFetch
    globalThis.WebSocket = OriginalWebSocket
  }
})
