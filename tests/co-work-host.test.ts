import assert from 'node:assert/strict'
import test from 'node:test'
import { createCoWorkHost } from '../src/host/co-work-host.js'
import type {
  Thread,
  ThreadMessage,
  ThreadValidationReason,
  WorkspaceValidationReason,
} from '../src/shared/contracts.js'
import { createFakeGateway } from './support/fake-gateway.js'

const openWorkspace = (baseUrl: string, path: string) =>
  fetch(`${baseUrl}/api/workspace`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ path }),
  })

const openThread = (baseUrl: string, threadId: string) =>
  fetch(`${baseUrl}/api/thread`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ threadId }),
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

test('opens a Thread and returns its Hermes history in order through the host', async () => {
  const messages: ThreadMessage[] = [
    { id: 'r1', role: 'user', text: 'Ship issue 03' },
    { id: 'r2', role: 'assistant', text: 'On it.' },
    { role: 'tool', text: 'ran tests' },
  ]
  const app = createCoWorkHost(createFakeGateway({
    openThread: async (threadId) => ({ kind: 'opened', history: { threadId, messages } }),
  }))
  const address = await app.listen(0)
  try {
    const body = await (await openThread(`http://127.0.0.1:${address.port}`, 's1')).json()
    assert.deepEqual(body, { status: 'opened', threadId: 's1', messages })
  } finally {
    await app.close()
  }
})

test('maps each Thread read error to a safe recovery message', async () => {
  const expected: Record<ThreadValidationReason, RegExp> = {
    'not-found': /no longer exists/i,
    unreadable: /could not be read/i,
  }
  for (const reason of Object.keys(expected) as ThreadValidationReason[]) {
    const app = createCoWorkHost(createFakeGateway({
      openThread: async () => ({ kind: 'error', reason }),
    }))
    const address = await app.listen(0)
    try {
      const body = await (await openThread(`http://127.0.0.1:${address.port}`, 's1')).json()
      assert.equal(body.status, 'error')
      assert.equal(body.reason, reason)
      assert.match(body.message, expected[reason])
    } finally {
      await app.close()
    }
  }
})

test('returns a safe unavailable Thread state when the gateway throws', async () => {
  const app = createCoWorkHost(createFakeGateway({
    openThread: async () => {
      throw new Error('history http://127.0.0.1:9119 failed with token=do-not-render')
    },
  }))
  const address = await app.listen(0)
  try {
    const response = await openThread(`http://127.0.0.1:${address.port}`, 's1')
    const body = await response.json()
    assert.equal(response.status, 200)
    assert.equal(body.status, 'error')
    assert.equal(body.reason, 'unavailable')
    assert.doesNotMatch(JSON.stringify(body), /token|do-not-render|127\.0\.0\.1:9119/i)
  } finally {
    await app.close()
  }
})

test('creates a Thread in the current Workspace cwd through the host', async () => {
  let createdWith: { cwd: string; title?: string } | undefined
  const app = createCoWorkHost(createFakeGateway({
    createThread: async (cwd, title) => { createdWith = { cwd, title }; return { kind: 'created', threadId: 'new-1' } },
  }))
  const address = await app.listen(0)
  try {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/thread/create`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ cwd: '/home/dev/project', title: 'Draft' }),
    })
    assert.deepEqual(await response.json(), { status: 'created', threadId: 'new-1' })
    assert.deepEqual(createdWith, { cwd: '/home/dev/project', title: 'Draft' })
  } finally {
    await app.close()
  }
})

test('returns a safe error when Thread creation fails', async () => {
  const app = createCoWorkHost(createFakeGateway({
    createThread: async () => { throw new Error('token=do-not-render') },
  }))
  const address = await app.listen(0)
  try {
    const body = await (await fetch(`http://127.0.0.1:${address.port}/api/thread/create`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ cwd: '/x' }),
    })).json()
    assert.equal(body.status, 'error')
    assert.doesNotMatch(JSON.stringify(body), /token|do-not-render/i)
  } finally {
    await app.close()
  }
})

test('submits a prompt and stops a turn through the host', async () => {
  const prompts: Array<{ threadId: string; text: string }> = []
  let stopped = ''
  const app = createCoWorkHost(createFakeGateway({
    submitPrompt: async (threadId, text) => { prompts.push({ threadId, text }) },
    stopThread: async (threadId) => { stopped = threadId },
  }))
  const address = await app.listen(0)
  const base = `http://127.0.0.1:${address.port}`
  try {
    const submit = await (await fetch(`${base}/api/thread/prompt`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ threadId: 't1', text: 'Do the thing' }),
    })).json()
    const stop = await (await fetch(`${base}/api/thread/stop`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ threadId: 't1' }),
    })).json()

    assert.deepEqual(submit, { status: 'submitted' })
    assert.deepEqual(stop, { status: 'stopped' })
    assert.deepEqual(prompts, [{ threadId: 't1', text: 'Do the thing' }])
    assert.equal(stopped, 't1')
  } finally {
    await app.close()
  }
})

test('streams a turn to the browser as Server-Sent Events', async () => {
  let subscribedThread = ''
  const app = createCoWorkHost(createFakeGateway({
    subscribe: (threadId, listener) => {
      subscribedThread = threadId
      queueMicrotask(() => {
        listener({ kind: 'turn-start' })
        listener({ kind: 'message-start', role: 'assistant' })
        listener({ kind: 'message-delta', text: 'Hel' })
        listener({ kind: 'message-delta', text: 'lo' })
        listener({ kind: 'tool-start', tool: 'shell' })
        listener({ kind: 'tool-end', tool: 'shell', summary: 'ran ls', details: 'a\nb' })
        listener({ kind: 'message-complete' })
        listener({ kind: 'turn-end' })
      })
      return () => {}
    },
  }))
  const address = await app.listen(0)
  try {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/thread/stream?threadId=t1`)
    assert.match(response.headers.get('content-type') ?? '', /text\/event-stream/)
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (!buffer.includes('"turn-end"')) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value)
    }
    await reader.cancel()

    const events = buffer
      .split('\n\n')
      .filter((block) => block.startsWith('data: '))
      .map((block) => JSON.parse(block.slice('data: '.length)))
    assert.equal(subscribedThread, 't1')
    assert.deepEqual(events.map((e) => e.kind), [
      'turn-start', 'message-start', 'message-delta', 'message-delta',
      'tool-start', 'tool-end', 'message-complete', 'turn-end',
    ])
    assert.equal(events.filter((e) => e.kind === 'message-delta').map((e) => e.text).join(''), 'Hello')
  } finally {
    await app.close()
  }
})

test('resolves an Approval with an explicit choice and never defaults to allow', async () => {
  const calls: Array<{ threadId: string; requestId: string; choice: string }> = []
  const app = createCoWorkHost(createFakeGateway({
    respondApproval: async (threadId, requestId, choice) => { calls.push({ threadId, requestId, choice }) },
  }))
  const address = await app.listen(0)
  const base = `http://127.0.0.1:${address.port}`
  const respond = (payload: unknown) => fetch(`${base}/api/thread/approval`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload),
  })
  try {
    const allow = await (await respond({ threadId: 's1', requestId: 'a1', choice: 'allow' })).json()
    const missing = await (await respond({ threadId: 's1', requestId: 'a2' })).json()

    assert.deepEqual(allow, { status: 'resolved', choice: 'allow' })
    assert.deepEqual(missing, { status: 'resolved', choice: 'deny' })
    assert.deepEqual(calls, [
      { threadId: 's1', requestId: 'a1', choice: 'allow' },
      { threadId: 's1', requestId: 'a2', choice: 'deny' },
    ])
  } finally {
    await app.close()
  }
})

test('reports a safe error when an Approval response fails', async () => {
  const app = createCoWorkHost(createFakeGateway({
    respondApproval: async () => { throw new Error('token=do-not-render') },
  }))
  const address = await app.listen(0)
  try {
    const body = await (await fetch(`http://127.0.0.1:${address.port}/api/thread/approval`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ threadId: 's1', requestId: 'a1', choice: 'allow' }),
    })).json()
    assert.equal(body.status, 'error')
    assert.doesNotMatch(JSON.stringify(body), /token|do-not-render/i)
  } finally {
    await app.close()
  }
})
