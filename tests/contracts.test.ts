import assert from 'node:assert/strict'
import test from 'node:test'
import {
  healthResponse,
  threadResponse,
  workspaceResponse,
  type Thread,
  type ThreadMessage,
  type ThreadValidationReason,
  type WorkspaceValidationReason,
} from '../src/shared/contracts.js'

test('ready health response identifies the Hermes runtime without a secret', () => {
  assert.deepEqual(healthResponse({ kind: 'ready', startedByCoWork: false }), {
    status: 'ready',
    runtime: 'Hermes',
    startedByCoWork: false,
  })
})

test('unavailable health response supplies a safe remedy', () => {
  assert.deepEqual(healthResponse({ kind: 'unavailable', remedy: 'Run hermes setup.' }), {
    status: 'unavailable',
    runtime: 'Hermes',
    remedy: 'Run hermes setup.',
  })
})

test('opened Workspace response carries the path and its Threads', () => {
  const threads: Thread[] = [
    { id: 't1', title: 'First', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' },
  ]
  assert.deepEqual(
    workspaceResponse({ kind: 'opened', workspace: { path: '/home/dev/x', threads } }),
    { status: 'opened', path: '/home/dev/x', threads },
  )
})

test('each Workspace validation error carries its reason and an actionable message', () => {
  const reasons: WorkspaceValidationReason[] = [
    'not-absolute', 'not-found', 'not-a-directory', 'unreadable',
  ]
  for (const reason of reasons) {
    const response = workspaceResponse({ kind: 'error', reason })
    assert.equal(response.status, 'error')
    assert.equal(response.reason, reason)
    assert.ok(response.status === 'error' && response.message.length > 0)
  }
})

test('opened Thread response carries the thread id and its messages', () => {
  const messages: ThreadMessage[] = [
    { id: 'r1', role: 'user', text: 'hi' },
    { role: 'assistant', text: 'hello' },
  ]
  assert.deepEqual(
    threadResponse({ kind: 'opened', history: { threadId: 's1', messages } }),
    { status: 'opened', threadId: 's1', messages },
  )
})

test('each Thread validation error carries its reason and a recovery message', () => {
  for (const reason of ['not-found', 'unreadable'] as ThreadValidationReason[]) {
    const response = threadResponse({ kind: 'error', reason })
    assert.equal(response.status, 'error')
    assert.equal(response.reason, reason)
    assert.ok(response.status === 'error' && response.message.length > 0)
  }
})
