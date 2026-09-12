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
