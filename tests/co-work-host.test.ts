import assert from 'node:assert/strict'
import test from 'node:test'
import { createCoWorkHost } from '../src/host/co-work-host.js'

test('returns Hermes readiness only through the loopback health endpoint', async () => {
  const app = createCoWorkHost({
    health: async () => ({ kind: 'ready', startedByCoWork: false }),
    close: async () => {},
  })
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
  const app = createCoWorkHost({
    health: async () => ({ kind: 'ready', startedByCoWork: false }),
    close: async () => {},
  })
  const address = await app.listen(0)
  assert.equal(address.address, '127.0.0.1')
  await app.close()
})

test('returns not found for an unknown route', async () => {
  const app = createCoWorkHost({
    health: async () => ({ kind: 'ready', startedByCoWork: false }),
    close: async () => {},
  })
  const address = await app.listen(0)
  const response = await fetch(`http://127.0.0.1:${address.port}/unknown`)
  assert.equal(response.status, 404)
  await app.close()
})
