import assert from 'node:assert/strict'
import test from 'node:test'
import { healthResponse } from '../src/shared/contracts.js'

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
