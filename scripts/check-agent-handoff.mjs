import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const repositoryRoot = process.cwd()
const requiredDocuments = [
  'AGENTS.md',
  'CONTEXT.md',
  '.agents/handoffs/README.md',
  '.agents/handoffs/current.md',
  '.agents/workflows/development.md',
  'CLAUDE.md',
  '.github/copilot-instructions.md',
  'docs/agents/hermes.md',
]

const readme = readFileSync(resolve(repositoryRoot, 'README.md'), 'utf8')
if (!readme.includes('.agents/handoffs/README.md')) {
  console.error('README is missing agent handoff guidance.')
  process.exit(1)
}

for (const documentPath of requiredDocuments) {
  if (!existsSync(resolve(repositoryRoot, documentPath))) {
    console.error(`Missing required agent document: ${documentPath}`)
    process.exit(1)
  }
}

const checkpointDocuments = [
  '.agents/handoffs/README.md',
  '.agents/workflows/development.md',
]

for (const documentPath of checkpointDocuments) {
  const document = readFileSync(resolve(repositoryRoot, documentPath), 'utf8')
  if (!document.includes('after every meaningful state change')) {
    console.error(`Missing handoff checkpoint rule: ${documentPath}`)
    process.exit(1)
  }
}

const adapterPaths = [
  'CLAUDE.md',
  '.github/copilot-instructions.md',
  'docs/agents/hermes.md',
]
const canonicalPointers = ['AGENTS.md', 'CONTEXT.md', '.agents/handoffs/current.md']

for (const adapterPath of adapterPaths) {
  const adapter = readFileSync(resolve(repositoryRoot, adapterPath), 'utf8')
  for (const pointer of canonicalPointers) {
    if (!adapter.includes(pointer)) {
      console.error(`Adapter is missing canonical pointer: ${adapterPath}`)
      process.exit(1)
    }
  }
}

const requiredHeadings = [
  '# Current Agent Handoff',
  '## Goal',
  '## State',
  '## Git',
  '## Changed Files',
  '## Verification',
  '## Next Action',
  '## Risks or Decisions Needed',
]
const currentHandoff = readFileSync(
  resolve(repositoryRoot, '.agents/handoffs/current.md'),
  'utf8',
)
const handoffLines = new Set(currentHandoff.split(/\r?\n/))

for (const heading of requiredHeadings) {
  if (!handoffLines.has(heading)) {
    console.error(`Missing handoff heading: ${heading}`)
    process.exit(1)
  }
}

console.log('Agent handoff protocol is valid.')
