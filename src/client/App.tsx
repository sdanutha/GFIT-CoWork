import { useCallback, useEffect, useRef, useState } from 'react'
import { workspaceErrorMessage } from '../shared/contracts.js'
import type { HealthResponse, Thread, WorkspaceResponse } from '../shared/contracts.js'

const unavailableHealth: HealthResponse = {
  status: 'unavailable',
  runtime: 'Hermes',
  remedy: 'Check your Hermes setup, then retry.',
}

const unavailableWorkspace: WorkspaceResponse = {
  status: 'error',
  reason: 'unavailable',
  message: workspaceErrorMessage('unavailable'),
}

export async function requestHealth(fetcher: typeof fetch = fetch): Promise<HealthResponse> {
  try {
    const response = await fetcher('/api/health')
    if (!response.ok) return unavailableHealth
    return await response.json() as HealthResponse
  } catch {
    return unavailableHealth
  }
}

export async function requestWorkspace(
  path: string,
  fetcher: typeof fetch = fetch,
): Promise<WorkspaceResponse> {
  try {
    const response = await fetcher('/api/workspace', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!response.ok) return unavailableWorkspace
    return await response.json() as WorkspaceResponse
  } catch {
    return unavailableWorkspace
  }
}

const recentWorkspacesKey = 'gfit-cowork:recent-workspaces'
const maxRecentWorkspaces = 8

export function readRecentWorkspaces(): string[] {
  try {
    const raw = globalThis.localStorage?.getItem(recentWorkspacesKey)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((p): p is string => typeof p === 'string') : []
  } catch {
    return []
  }
}

function rememberWorkspace(path: string): string[] {
  const next = [path, ...readRecentWorkspaces().filter((p) => p !== path)].slice(0, maxRecentWorkspaces)
  try {
    globalThis.localStorage?.setItem(recentWorkspacesKey, JSON.stringify(next))
  } catch {
    // A navigation preference is disposable; ignore storage failures.
  }
  return next
}

function formatRecency(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Unknown'
  return new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeZone: 'UTC' }).format(date)
}

type WorkspaceState =
  | { phase: 'idle' }
  | { phase: 'opening' }
  | { phase: 'opened'; path: string; threads: Thread[] }
  | { phase: 'error'; message: string }

export function WorkspaceBrowser({ open = requestWorkspace }: { open?: typeof requestWorkspace }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [recent, setRecent] = useState<string[]>(() => readRecentWorkspaces())
  const [state, setState] = useState<WorkspaceState>({ phase: 'idle' })

  const openWorkspace = useCallback(async (path: string) => {
    const trimmed = path.trim()
    if (trimmed.length === 0) return
    setState({ phase: 'opening' })
    const result = await open(trimmed)
    if (result.status === 'opened') {
      setRecent(rememberWorkspace(result.path))
      setState({ phase: 'opened', path: result.path, threads: result.threads })
    } else {
      setState({ phase: 'error', message: result.message })
    }
  }, [open])

  const openFromInput = useCallback(() => {
    void openWorkspace(inputRef.current?.value ?? '')
  }, [openWorkspace])

  return (
    <section className="workspace" aria-labelledby="workspace-heading">
      <h2 id="workspace-heading">Workspace</h2>
      <form
        className="workspace-open"
        onSubmit={(event) => { event.preventDefault(); openFromInput() }}
      >
        <label htmlFor="workspace-path">Absolute folder path</label>
        <div className="workspace-open-row">
          <input
            id="workspace-path"
            name="path"
            className="workspace-path"
            type="text"
            inputMode="text"
            placeholder="/Users/you/project"
            ref={inputRef}
            defaultValue=""
          />
          <button className="workspace-open-button" type="button" onClick={openFromInput}>
            Open
          </button>
        </div>
      </form>

      {recent.length > 0 && (
        <nav className="workspace-recent" aria-label="Recent Workspaces">
          <p className="workspace-recent-heading">Recent</p>
          <ul>
            {recent.map((path) => (
              <li key={path}>
                <button
                  className="workspace-recent-item"
                  type="button"
                  onClick={() => {
                    if (inputRef.current) inputRef.current.value = path
                    void openWorkspace(path)
                  }}
                >
                  {path}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      )}

      {state.phase === 'opening' && (
        <p className="workspace-status" role="status">Opening Workspace…</p>
      )}

      {state.phase === 'error' && (
        <p className="workspace-status workspace-status--error" role="alert">{state.message}</p>
      )}

      {state.phase === 'opened' && (
        <div className="thread-list">
          <p className="thread-list-heading">
            Threads in <span className="workspace-path-name">{state.path}</span>
          </p>
          {state.threads.length === 0 ? (
            <p className="workspace-status" role="status">No Threads in this Workspace yet.</p>
          ) : (
            <ul>
              {state.threads.map((thread) => (
                <li key={thread.id} className="thread-item">
                  <span className="thread-title">{thread.title}</span>
                  <time className="thread-recency" dateTime={thread.updatedAt}>
                    {formatRecency(thread.updatedAt)}
                  </time>
                  <span className={`thread-activity thread-activity--${thread.activity}`}>
                    {thread.activity === 'live' ? 'Live' : 'Idle'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}

type AppProps = {
  health?: HealthResponse
  onRetry?: () => void
}

export function App({ health, onRetry }: AppProps) {
  const [currentHealth, setCurrentHealth] = useState<HealthResponse | null>(health ?? null)
  const loadHealth = useCallback(async () => {
    setCurrentHealth(null)
    setCurrentHealth(await requestHealth())
  }, [])

  useEffect(() => {
    if (health === undefined) void loadHealth()
  }, [health, loadHealth])

  const retry = onRetry ?? (() => { void loadHealth() })

  return (
    <main className="app-shell">
      <article className="status-card" aria-labelledby="product-name">
        <div className="brand-mark" aria-hidden="true">G</div>
        <p className="eyebrow">Local Hermes workspace</p>
        <h1 id="product-name">GFIT CoWork</h1>
        {currentHealth === null ? (
          <section className="status-panel">
            <p className="status-heading" role="status">Checking Hermes…</p>
            <p className="status-copy">Connecting to the Hermes runtime on this machine.</p>
          </section>
        ) : currentHealth.status === 'ready' ? (
          <section className="status-panel">
            <p className="status-heading" role="status">Hermes is ready</p>
            <p className="status-copy">
              GFIT CoWork is connected to your local Hermes runtime.
            </p>
          </section>
        ) : (
          <section className="status-panel status-panel--unavailable">
            <p className="status-heading" role="alert">Hermes is unavailable</p>
            <p className="status-copy">{currentHealth.remedy}</p>
            <button className="retry-button" type="button" onClick={retry}>Retry</button>
          </section>
        )}
        {currentHealth?.status === 'ready' && <WorkspaceBrowser />}
        <p className="local-note">Runs locally on this machine.</p>
      </article>
    </main>
  )
}
