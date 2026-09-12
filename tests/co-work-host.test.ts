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

test('does not expose gateway connection details or credential-like values', async () => {
  const app = createCoWorkHost({
    health: async () => ({
      kind: 'unavailable',
      remedy: 'Gateway http://127.0.0.1:9119 failed with token=do-not-render and password=secret.',
    }),
    close: async () => {},
  })
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

test('closes the HTTP listener before closing the Hermes gateway exactly once', async () => {
  let endpoint = ''
  let gatewayCloses = 0
  const app = createCoWorkHost({
    health: async () => ({ kind: 'ready', startedByCoWork: false }),
    close: async () => {
      gatewayCloses += 1
      await assert.rejects(fetch(endpoint))
    },
  })
  const address = await app.listen(0)
  endpoint = `http://127.0.0.1:${address.port}/api/health`

  await Promise.all([app.close(), app.close()])

  assert.equal(gatewayCloses, 1)
})

test('closes the Hermes gateway once when the HTTP listener was never started', async () => {
  let gatewayCloses = 0
  const app = createCoWorkHost({
    health: async () => ({ kind: 'ready', startedByCoWork: false }),
    close: async () => { gatewayCloses += 1 },
  })

  await Promise.all([app.close(), app.close()])

  assert.equal(gatewayCloses, 1)
})
