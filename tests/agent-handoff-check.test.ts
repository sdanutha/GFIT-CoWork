import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import test from 'node:test'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'

test('validates the cross-agent handoff protocol', () => {
  assert.doesNotThrow(() => {
    execFileSync('npm', ['run', 'check:agents'], { stdio: 'pipe' })
  })
})

test('checkpoints the handoff after every meaningful state change', () => {
  const checkpointDocuments = [
    '.agents/handoffs/README.md',
    '.agents/workflows/development.md',
  ]

  for (const documentPath of checkpointDocuments) {
    const document = readFileSync(resolve(process.cwd(), documentPath), 'utf8')
    assert.match(document, /after every meaningful state change/)
  }
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

test('onboards agents through the canonical handoff protocol', () => {
  const readme = readFileSync(resolve(process.cwd(), 'README.md'), 'utf8')
  assert.match(readme, /\.agents\/handoffs\/README\.md/)
})

test('rejects a README without the handoff protocol pointer', () => {
  const fixtureDirectory = mkdtempSync(join(tmpdir(), 'gfit-cowork-agent-check-'))
  writeFileSync(join(fixtureDirectory, 'README.md'), '# Fixture\n')

  try {
    assert.throws(
      () =>
        execFileSync(
          process.execPath,
          [resolve(process.cwd(), 'scripts/check-agent-handoff.mjs')],
          { cwd: fixtureDirectory, encoding: 'utf8', stdio: 'pipe' },
        ),
      (error: unknown) => {
        assert.equal(error && typeof error === 'object' && 'status' in error ? error.status : undefined, 1)
        assert.equal(
          error && typeof error === 'object' && 'stderr' in error ? error.stderr : undefined,
          'README is missing agent handoff guidance.\n',
        )
        return true
      },
    )
  } finally {
    rmSync(fixtureDirectory, { recursive: true, force: true })
  }
})
