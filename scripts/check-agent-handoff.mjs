import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const repositoryRoot = process.cwd()
const requiredDocuments = [
  'AGENTS.md',
  'CONTEXT.md',
  '.agents/handoffs/README.md',
  '.agents/handoffs/current.md',
  '.agents/workflows/development.md',
]

for (const documentPath of requiredDocuments) {
  if (!existsSync(resolve(repositoryRoot, documentPath))) {
    console.error(`Missing required agent document: ${documentPath}`)
    process.exit(1)
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
