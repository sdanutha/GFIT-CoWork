import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { resolve } from 'node:path'

test('validates the cross-agent handoff protocol', () => {
  assert.doesNotThrow(() => {
    execFileSync('npm', ['run', 'check:agents'], { stdio: 'pipe' })
  })
})

test('keeps every tool adapter pointed at the canonical handoff documents', () => {
  const adapterPaths = [
    'CLAUDE.md',
    '.github/copilot-instructions.md',
    'docs/agents/hermes.md',
  ]

  for (const adapterPath of adapterPaths) {
    const adapter = readFileSync(resolve(process.cwd(), adapterPath), 'utf8')
    assert.match(adapter, /AGENTS\.md/)
    assert.match(adapter, /CONTEXT\.md/)
    assert.match(adapter, /\.agents\/handoffs\/current\.md/)
  }
})
