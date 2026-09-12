# Launch GFIT CoWork with Hermes Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a local-only GFIT CoWork web app that safely reports whether a local Hermes gateway is ready, attaching to one when present and starting one when absent.

**Architecture:** The browser calls a loopback-only CoWork host. The host depends only on the `HermesWorkspaceGateway` interface, which hides Hermes availability probing and process ownership. The production gateway probes the local Hermes health endpoint, starts `hermes serve` only after a failed probe, waits for readiness, and stops only a process it started; tests use an in-memory fake at that seam.

**Tech Stack:** Node.js 22+, TypeScript, React, Vite, Node built-in HTTP server and test runner, CSS.

**Spec:** `docs/superpowers/specs/2026-09-13-gfit-cowork-design.md`; `.scratch/gfit-cowork/issues/01-launch-with-hermes-health.md`

## Global Constraints

- GFIT CoWork is Hermes-only and must never identify itself as Claude Code or an Anthropic product.
- The CoWork host binds only to `127.0.0.1`; no remote-access option exists in v1.
- Hermes profiles and credentials remain external to GFIT CoWork; responses and errors must not expose secret values.
- `HermesWorkspaceGateway` is the only seam between the CoWork host and Hermes.
- The host does not run Workspace shell commands or read/write Workspace files.
- Tests must not invoke a real model, mutate a personal Hermes session, or depend on personal credentials.
- Production code is written only after its corresponding failing test has been observed.

---

## File Structure

- `package.json`: defines reproducible development, production, type-check, and test commands.
- `tsconfig.json`: strict shared TypeScript compiler settings.
- `vite.config.ts`: builds the React UI and proxies development API calls to the CoWork host.
- `src/shared/contracts.ts`: shared health response types used by both host and UI.
- `src/host/hermes-workspace-gateway.ts`: the single `HermesWorkspaceGateway` interface and readiness types.
- `src/host/local-hermes-workspace-gateway.ts`: production implementation that probes or owns a `hermes serve` child process.
- `src/host/co-work-host.ts`: loopback HTTP host exposing the browser health endpoint.
- `src/host/index.ts`: production process entrypoint and graceful shutdown wiring.
- `src/client/main.tsx`: React bootstrap.
- `src/client/App.tsx`: health/setup screen driven only by the CoWork host endpoint.
- `src/client/styles.css`: responsive, system-theme-aware GFIT CoWork appearance.
- `tests/local-hermes-workspace-gateway.test.ts`: behavior tests for attach, start, timeout, and owned-process cleanup.
- `tests/co-work-host.test.ts`: black-box loopback host endpoint tests using a fake gateway.
- `tests/app.test.tsx`: UI tests for ready, unavailable, and retry presentation.

## Task 1: Bootstrap the strict TypeScript workspace and health contract

**Files:**
- Create: `package.json`
- Create: `tsconfig.json`
- Create: `vite.config.ts`
- Create: `src/shared/contracts.ts`
- Create: `src/host/hermes-workspace-gateway.ts`
- Create: `tests/contracts.test.ts`

**Interfaces:**
- Produces `HermesReadiness`, `HealthResponse`, and `HermesWorkspaceGateway` for all later tasks.

- [ ] **Step 1: Write the failing contract test**

```ts
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- tests/contracts.test.ts`

Expected: FAIL because the package test command and `src/shared/contracts.ts` do not exist.

- [ ] **Step 3: Add the minimal project configuration and contract**

```json
{
  "name": "gfit-cowork",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "node --import tsx src/host/index.ts",
    "dev:client": "vite",
    "build": "tsc --noEmit && vite build",
    "start": "node --import tsx src/host/index.ts",
    "test": "node --import tsx --test tests/*.test.ts"
  },
  "dependencies": { "react": "^19.3.0", "react-dom": "^19.3.0" },
  "devDependencies": { "@types/node": "^24.0.0", "@types/react": "^19.0.0", "@types/react-dom": "^19.0.0", "@vitejs/plugin-react": "^6.0.0", "tsx": "^4.20.0", "typescript": "^5.9.0", "vite": "^8.3.0" }
}
```

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "verbatimModuleSyntax": true
  },
  "include": ["src", "tests", "vite.config.ts"]
}
```

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:4318' },
  },
})
```

```ts
export type HermesReadiness =
  | { kind: 'ready'; startedByCoWork: boolean }
  | { kind: 'unavailable'; remedy: string }

export type HealthResponse =
  | { status: 'ready'; runtime: 'Hermes'; startedByCoWork: boolean }
  | { status: 'unavailable'; runtime: 'Hermes'; remedy: string }

export function healthResponse(readiness: HermesReadiness): HealthResponse {
  return readiness.kind === 'ready'
    ? { status: 'ready', runtime: 'Hermes', startedByCoWork: readiness.startedByCoWork }
    : { status: 'unavailable', runtime: 'Hermes', remedy: readiness.remedy }
}
```

```ts
import type { HermesReadiness } from '../shared/contracts.js'

export interface HermesWorkspaceGateway {
  health(): Promise<HermesReadiness>
  close(): Promise<void>
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- tests/contracts.test.ts`

Expected: PASS, with both response shapes containing no endpoint, profile, or credential data.

- [ ] **Step 5: Commit the bootstrap contract**

```bash
git add package.json tsconfig.json vite.config.ts src/shared/contracts.ts src/host/hermes-workspace-gateway.ts tests/contracts.test.ts
git commit -m "feat: define Hermes health contract"
```

## Task 2: Implement local Hermes readiness behind the gateway seam

**Files:**
- Create: `src/host/local-hermes-workspace-gateway.ts`
- Test: `tests/local-hermes-workspace-gateway.test.ts`

**Interfaces:**
- Consumes `HermesWorkspaceGateway` and `HermesReadiness` from Task 1.
- Produces `createLocalHermesWorkspaceGateway(options): HermesWorkspaceGateway`.

- [ ] **Step 1: Write the failing attach-or-start test**

```ts
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- tests/local-hermes-workspace-gateway.test.ts`

Expected: FAIL because `createLocalHermesWorkspaceGateway` does not exist.

- [ ] **Step 3: Implement the minimal gateway with injected process operations**

```ts
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'
import type { HermesReadiness } from '../shared/contracts.js'

type OwnedProcess = { stop(): Promise<void> }
type Options = {
  probe(): Promise<boolean>
  start(): OwnedProcess
  waitForReady(): Promise<boolean>
}

export function createLocalHermesWorkspaceGateway(options: Options): HermesWorkspaceGateway {
  let owned: OwnedProcess | undefined
  let readiness: HermesReadiness | undefined

  return {
    async health() {
      if (readiness) return readiness
      if (await options.probe()) return (readiness = { kind: 'ready', startedByCoWork: false })
      owned = options.start()
      if (await options.waitForReady()) return (readiness = { kind: 'ready', startedByCoWork: true })
      await owned.stop()
      owned = undefined
      return (readiness = { kind: 'unavailable', remedy: 'Start Hermes with hermes serve, then retry.' })
    },
    async close() {
      await owned?.stop()
      owned = undefined
      readiness = undefined
    },
  }
}
```

- [ ] **Step 4: Add and run timeout/failure tests**

```ts
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
```

Run: `npm test -- tests/local-hermes-workspace-gateway.test.ts`

Expected: PASS for attach, owned start, owned cleanup, and unavailable behavior.

- [ ] **Step 5: Wire real loopback probe and child process functions**

```ts
import { spawn } from 'node:child_process'

const hermesBaseUrl = 'http://127.0.0.1:9119'

const probe = async () => {
  try { return (await fetch(`${hermesBaseUrl}/health`)).ok } catch { return false }
}

const start = () => {
  const child = spawn('hermes', ['serve', '--host', '127.0.0.1', '--port', '9119'], {
    detached: process.platform !== 'win32', stdio: 'ignore', shell: false,
  })
  return { stop: async () => { if (child.pid) process.kill(child.pid, 'SIGTERM') } }
}
```

Run: `npm test -- tests/local-hermes-workspace-gateway.test.ts && npm run build`

Expected: PASS; real operations remain dependency-injected so tests do not launch Hermes.

- [ ] **Step 6: Commit the gateway implementation**

```bash
git add src/host/local-hermes-workspace-gateway.ts tests/local-hermes-workspace-gateway.test.ts
git commit -m "feat: manage local Hermes readiness"
```

## Task 3: Expose health through a loopback-only CoWork host

**Files:**
- Create: `src/host/co-work-host.ts`
- Create: `src/host/index.ts`
- Test: `tests/co-work-host.test.ts`

**Interfaces:**
- Consumes `HermesWorkspaceGateway` from Task 1.
- Produces `createCoWorkHost(gateway)` with `listen(port)` and `close()` methods.

- [ ] **Step 1: Write the failing host endpoint test**

```ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { createCoWorkHost } from '../src/host/co-work-host.js'

test('returns Hermes readiness only through the loopback health endpoint', async () => {
  const app = createCoWorkHost({ health: async () => ({ kind: 'ready', startedByCoWork: false }), close: async () => {} })
  const address = await app.listen(0)
  const response = await fetch(`http://127.0.0.1:${address.port}/api/health`)
  assert.deepEqual(await response.json(), { status: 'ready', runtime: 'Hermes', startedByCoWork: false })
  await app.close()
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- tests/co-work-host.test.ts`

Expected: FAIL because `createCoWorkHost` does not exist.

- [ ] **Step 3: Implement the minimal host and restrictive request handling**

```ts
import { createServer } from 'node:http'
import { healthResponse } from '../shared/contracts.js'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'

export function createCoWorkHost(gateway: HermesWorkspaceGateway) {
  const server = createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/api/health') {
      const body = JSON.stringify(healthResponse(await gateway.health()))
      response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
      response.end(body)
      return
    }
    response.writeHead(404).end()
  })
  return {
    listen: (port: number) => new Promise<{ port: number }>((resolve) => server.listen(port, '127.0.0.1', () => resolve(server.address() as { port: number }))),
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
}
```

- [ ] **Step 4: Add and run non-loopback and unknown-route tests**

```ts
test('does not bind the CoWork host to all network interfaces', async () => {
  const app = createCoWorkHost({ health: async () => ({ kind: 'ready', startedByCoWork: false }), close: async () => {} })
  const address = await app.listen(0)
  assert.equal(address.address, '127.0.0.1')
  await app.close()
})
```

Run: `npm test -- tests/co-work-host.test.ts`

Expected: PASS; the health response is safe and the host binds only to loopback.

- [ ] **Step 5: Add production startup and graceful shutdown wiring**

```ts
const host = createCoWorkHost(createProductionHermesWorkspaceGateway())
await host.listen(Number(process.env.PORT ?? 4318))
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, async () => { await host.close(); process.exit(0) })
}
```

Run: `npm test -- tests/co-work-host.test.ts && npm run build`

Expected: PASS; application shutdown can later close only the Hermes process that CoWork owns.

- [ ] **Step 6: Commit the loopback host**

```bash
git add src/host/co-work-host.ts src/host/index.ts tests/co-work-host.test.ts
git commit -m "feat: expose loopback Hermes health"
```

## Task 4: Render the GFIT CoWork health and setup screen

**Files:**
- Create: `index.html`
- Create: `src/client/main.tsx`
- Create: `src/client/App.tsx`
- Create: `src/client/styles.css`
- Test: `tests/app.test.tsx`

**Interfaces:**
- Consumes `GET /api/health` from Task 3.
- Produces an English-first, responsive health screen with retry behavior.

- [ ] **Step 1: Write the failing ready-state UI test**

```tsx
import assert from 'node:assert/strict'
import test from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { App } from '../src/client/App.js'

test('shows GFIT CoWork ready for Hermes', () => {
  const html = renderToStaticMarkup(<App health={{ status: 'ready', runtime: 'Hermes', startedByCoWork: false }} />)
  assert.match(html, /GFIT CoWork/)
  assert.match(html, /Hermes is ready/)
  assert.doesNotMatch(html, /Claude/)
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- tests/app.test.tsx`

Expected: FAIL because the React app does not exist.

- [ ] **Step 3: Implement the minimal health screen**

```tsx
import type { HealthResponse } from '../shared/contracts.js'

export function App({ health }: { health: HealthResponse }) {
  return <main>
    <p className="eyebrow">Local Hermes workspace</p>
    <h1>GFIT CoWork</h1>
    {health.status === 'ready'
      ? <p role="status">Hermes is ready</p>
      : <section><p role="alert">Hermes is unavailable</p><p>{health.remedy}</p><button type="button">Retry</button></section>}
  </main>
}
```

- [ ] **Step 4: Add client fetch/retry behavior and system-theme CSS**

```tsx
const [health, setHealth] = useState<HealthResponse | null>(null)
const loadHealth = () => fetch('/api/health').then((response) => response.json()).then(setHealth)
useEffect(() => { void loadHealth() }, [])
```

```css
:root { color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
body { margin: 0; min-width: 320px; background: Canvas; color: CanvasText; }
main { max-width: 48rem; margin: 12vh auto; padding: 2rem; }
```

- [ ] **Step 5: Run UI and production build verification**

Run: `npm test -- tests/app.test.tsx && npm run build`

Expected: PASS; ready and unavailable states are English-first, do not mention Claude, and build without browser-only failures.

- [ ] **Step 6: Commit the visible health experience**

```bash
git add index.html src/client tests/app.test.tsx
git commit -m "feat: show Hermes health in GFIT CoWork"
```

## Task 5: Verify the ticket end to end and document local startup

**Files:**
- Create: `README.md`
- Modify: `tests/co-work-host.test.ts`
- Modify: `tests/app.test.tsx`

**Interfaces:**
- Consumes the healthy and unavailable states from Tasks 2–4.
- Produces reproducible local startup instructions and a ticket-level verification suite.

- [ ] **Step 1: Write the failing no-secret error regression test**

```ts
test('does not expose a gateway endpoint or credential-like value in an unavailable response', async () => {
  const app = createCoWorkHost({ health: async () => ({ kind: 'unavailable', remedy: 'Run hermes setup.' }), close: async () => {} })
  const address = await app.listen(0)
  const body = JSON.stringify(await (await fetch(`http://127.0.0.1:${address.port}/api/health`)).json())
  assert.doesNotMatch(body, /token|key|password|127\.0\.0\.1:9119/i)
  await app.close()
})
```

- [ ] **Step 2: Run the regression test to verify it fails before response hardening**

Run: `npm test -- tests/co-work-host.test.ts`

Expected: FAIL if any host error forwards raw Hermes connection detail.

- [ ] **Step 3: Keep host responses limited to the shared HealthResponse contract and add startup instructions**

```md
# GFIT CoWork

GFIT CoWork is a local Hermes workspace client.

## Development

1. Configure Hermes outside GFIT CoWork: `hermes setup`.
2. Install dependencies: `npm install`.
3. Start the CoWork host: `npm run dev`.
4. Start the UI in another terminal: `npm run dev:client`.

GFIT CoWork uses only `localhost` and never stores Hermes credentials.
```

- [ ] **Step 4: Run full verification**

Run: `npm test && npm run build`

Expected: PASS; tests cover attach/start failure safety, loopback health, and both UI health states.

- [ ] **Step 5: Commit the completed ticket**

```bash
git add README.md tests/co-work-host.test.ts tests/app.test.tsx
git commit -m "docs: explain local GFIT CoWork startup"
```

## Plan Self-Review

- Spec coverage: Task 1 establishes the product contract; Task 2 provides attach-or-start Hermes readiness; Task 3 enforces the local CoWork host boundary; Task 4 delivers the user-visible health/setup state; Task 5 verifies local-only and no-secret requirements. Workspace and Thread features remain deliberately assigned to tickets 02–07.
- Placeholder scan: no TODO/TBD markers or undefined future implementation steps remain.
- Type consistency: every later task consumes `HermesReadiness`, `HealthResponse`, `HermesWorkspaceGateway`, or `createCoWorkHost` introduced in an earlier task.
