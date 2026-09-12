import assert from 'node:assert/strict'
import test from 'node:test'
import { createLocalHermesWorkspaceGateway } from '../src/host/local-hermes-workspace-gateway.js'

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
