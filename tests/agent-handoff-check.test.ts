import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import test from 'node:test'

test('validates the cross-agent handoff protocol', () => {
  assert.doesNotThrow(() => {
    execFileSync('npm', ['run', 'check:agents'], { stdio: 'pipe' })
  })
})
