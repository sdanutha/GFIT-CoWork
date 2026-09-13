import assert from 'node:assert/strict'
import test from 'node:test'
import { createCoWorkHost } from '../src/host/co-work-host.js'
import type { Thread, WorkspaceValidationReason } from '../src/shared/contracts.js'
import { createFakeGateway } from './support/fake-gateway.js'

const openWorkspace = (baseUrl: string, path: string) =>
  fetch(`${baseUrl}/api/workspace`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ path }),
  })

test('returns Hermes readiness only through the loopback health endpoint', async () => {
  const app = createCoWorkHost(createFakeGateway())
  const address = await app.listen(0)
  const response = await fetch(`http://127.0.0.1:${address.port}/api/health`)
  assert.deepEqual(await response.json(), {
    status: 'ready',
    runtime: 'Hermes',
    startedByCoWork: false,
  })
  await app.close()
})

test('does not bind the CoWork host to all network interfaces', async () => {
  const app = createCoWorkHost(createFakeGateway())
  const address = await app.listen(0)
  assert.equal(address.address, '127.0.0.1')
  await app.close()
})

test('returns not found for an unknown route', async () => {
  const app = createCoWorkHost(createFakeGateway())
  const address = await app.listen(0)
  const response = await fetch(`http://127.0.0.1:${address.port}/unknown`)
  assert.equal(response.status, 404)
  await app.close()
})

test('does not expose gateway connection details or credential-like values', async () => {
  const app = createCoWorkHost(createFakeGateway({
    health: async () => ({
      kind: 'unavailable',
      remedy: 'Gateway http://127.0.0.1:9119 failed with token=do-not-render and password=secret.',
    }),
  }))
  const address = await app.listen(0)
  try {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/health`)
    const body = JSON.stringify(await response.json())

    assert.equal(response.status, 200)
    assert.doesNotMatch(body, /token|key|password|127\.0\.0\.1:9119|do-not-render|secret/i)
    assert.match(body, /Hermes/)
  } finally {
    await app.close()
  }
})

test('returns safe unavailable health when the gateway throws credential-like details', async () => {
  const app = createCoWorkHost(createFakeGateway({
    health: async () => {
      throw new Error('Hermes gateway http://127.0.0.1:9119 failed with token=do-not-render')
    },
  }))
  const address = await app.listen(0)

  try {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/health`)
    const body = JSON.stringify(await response.json())

    assert.equal(response.status, 200)
    assert.deepEqual(JSON.parse(body), {
      status: 'unavailable',
      runtime: 'Hermes',
      remedy: 'Check your Hermes setup, then retry.',
    })
    assert.doesNotMatch(body, /token|key|password|127\.0\.0\.1:9119|do-not-render/i)
  } finally {
    await app.close()
  }
})

test('closes the HTTP listener before closing the Hermes gateway exactly once', async () => {
  let endpoint = ''
  let gatewayCloses = 0
  const app = createCoWorkHost(createFakeGateway({
    close: async () => {
      gatewayCloses += 1
      await assert.rejects(fetch(endpoint))
    },
  }))
  const address = await app.listen(0)
  endpoint = `http://127.0.0.1:${address.port}/api/health`

  await Promise.all([app.close(), app.close()])

  assert.equal(gatewayCloses, 1)
})

test('closes the Hermes gateway once when the HTTP listener was never started', async () => {
  let gatewayCloses = 0
  const app = createCoWorkHost(createFakeGateway({
    close: async () => { gatewayCloses += 1 },
  }))

  await Promise.all([app.close(), app.close()])

  assert.equal(gatewayCloses, 1)
})

test('opens a Workspace and returns its scoped Threads through the host', async () => {
  const threads: Thread[] = [
    { id: 't1', title: 'Refactor gateway', updatedAt: '2026-09-12T10:00:00.000Z', activity: 'idle' },
    { id: 't2', title: 'Investigate flake', updatedAt: '2026-09-13T08:30:00.000Z', activity: 'live' },
  ]
  const app = createCoWorkHost(createFakeGateway({
    openWorkspace: async (path) => ({ kind: 'opened', workspace: { path, threads } }),
  }))
  const address = await app.listen(0)
  try {
    const response = await openWorkspace(`http://127.0.0.1:${address.port}`, '/home/dev/project')
    assert.deepEqual(await response.json(), {
      status: 'opened',
      path: '/home/dev/project',
      threads,
    })
  } finally {
    await app.close()
  }
})

test('scopes the Thread list to the requested Workspace path', async () => {
  const byWorkspace: Record<string, Thread[]> = {
    '/home/dev/a': [{ id: 'a1', title: 'A only', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' }],
    '/home/dev/b': [{ id: 'b1', title: 'B only', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' }],
  }
  const app = createCoWorkHost(createFakeGateway({
    openWorkspace: async (path) => ({
      kind: 'opened',
      workspace: { path, threads: byWorkspace[path] ?? [] },
    }),
  }))
  const address = await app.listen(0)
  const base = `http://127.0.0.1:${address.port}`
  try {
    const a = await (await openWorkspace(base, '/home/dev/a')).json()
    const b = await (await openWorkspace(base, '/home/dev/b')).json()
    assert.deepEqual(a.threads, byWorkspace['/home/dev/a'])
    assert.deepEqual(b.threads, byWorkspace['/home/dev/b'])
  } finally {
    await app.close()
  }
})

test('maps each path validation error to a safe, actionable message', async () => {
  const expected: Record<WorkspaceValidationReason, RegExp> = {
    'not-absolute': /absolute folder path/i,
    'not-found': /does not exist/i,
    'not-a-directory': /not a folder/i,
    unreadable: /cannot be read/i,
  }
  for (const reason of Object.keys(expected) as WorkspaceValidationReason[]) {
    const app = createCoWorkHost(createFakeGateway({
      openWorkspace: async () => ({ kind: 'error', reason }),
    }))
    const address = await app.listen(0)
    try {
      const body = await (await openWorkspace(`http://127.0.0.1:${address.port}`, '/x')).json()
      assert.equal(body.status, 'error')
      assert.equal(body.reason, reason)
      assert.match(body.message, expected[reason])
    } finally {
      await app.close()
    }
  }
})

test('returns a safe unavailable Workspace state when the gateway throws', async () => {
  const app = createCoWorkHost(createFakeGateway({
    openWorkspace: async () => {
      throw new Error('sessions http://127.0.0.1:9119 failed with token=do-not-render')
    },
  }))
  const address = await app.listen(0)
  try {
    const response = await openWorkspace(`http://127.0.0.1:${address.port}`, '/home/dev/project')
    const body = await response.json()
    assert.equal(response.status, 200)
    assert.equal(body.status, 'error')
    assert.equal(body.reason, 'unavailable')
    assert.match(body.message, /Hermes/)
    assert.doesNotMatch(JSON.stringify(body), /token|do-not-render|127\.0\.0\.1:9119/i)
  } finally {
    await app.close()
  }
})

test('treats a malformed request body as an empty Workspace path', async () => {
  let received: string | undefined
  const app = createCoWorkHost(createFakeGateway({
    openWorkspace: async (path) => {
      received = path
      return { kind: 'error', reason: 'not-absolute' }
    },
  }))
  const address = await app.listen(0)
  try {
    await fetch(`http://127.0.0.1:${address.port}/api/workspace`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: 'not json',
    })
    assert.equal(received, '')
  } finally {
    await app.close()
  }
})
