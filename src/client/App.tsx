import { useCallback, useEffect, useRef, useState } from 'react'
import { threadErrorMessage, workspaceErrorMessage } from '../shared/contracts.js'
import type {
  HealthResponse,
  Thread,
  ThreadMessage,
  ThreadResponse,
  WorkspaceResponse,
} from '../shared/contracts.js'

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

const unavailableThread: ThreadResponse = {
  status: 'error',
  reason: 'unavailable',
  message: threadErrorMessage('unavailable'),
}

export async function requestThread(
  threadId: string,
  fetcher: typeof fetch = fetch,
): Promise<ThreadResponse> {
  try {
    const response = await fetcher('/api/thread', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ threadId }),
    })
    if (!response.ok) return unavailableThread
    return await response.json() as ThreadResponse
  } catch {
    return unavailableThread
  }
}

const recentWorkspacesKey = 'gfit-cowork:recent-workspaces'
const lastViewKey = 'gfit-cowork:last-view'
const maxRecentWorkspaces = 8

type LastView = { workspacePath?: string; threadId?: string }

export function readLastView(): LastView {
  try {
    const raw = globalThis.localStorage?.getItem(lastViewKey)
    const parsed = raw ? JSON.parse(raw) : {}
    if (typeof parsed !== 'object' || parsed === null) return {}
    const view: LastView = {}
    if (typeof (parsed as LastView).workspacePath === 'string') view.workspacePath = (parsed as LastView).workspacePath
    if (typeof (parsed as LastView).threadId === 'string') view.threadId = (parsed as LastView).threadId
    return view
  } catch {
    return {}
  }
}

function rememberLastView(view: LastView): void {
  try {
    globalThis.localStorage?.setItem(lastViewKey, JSON.stringify(view))
  } catch {
    // A navigation preference is disposable; ignore storage failures.
  }
}

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

type ThreadViewState =
  | { phase: 'loading' }
  | { phase: 'loaded'; messages: ThreadMessage[] }
  | { phase: 'error'; message: string }

export function ThreadView({
  threadId,
  open = requestThread,
}: { threadId: string; open?: typeof requestThread }) {
  const [state, setState] = useState<ThreadViewState>({ phase: 'loading' })

  useEffect(() => {
    let active = true
    setState({ phase: 'loading' })
    void open(threadId).then((result) => {
      if (!active) return
      setState(result.status === 'opened'
        ? { phase: 'loaded', messages: result.messages }
        : { phase: 'error', message: result.message })
    })
    return () => { active = false }
  }, [threadId, open])

  return (
    <section className="thread-view" aria-labelledby="thread-view-heading">
      <h3 id="thread-view-heading" className="thread-view-heading">Thread history</h3>
      {state.phase === 'loading' && (
        <p className="workspace-status" role="status">Loading Thread…</p>
      )}
      {state.phase === 'error' && (
        <p className="workspace-status workspace-status--error" role="alert">{state.message}</p>
      )}
      {state.phase === 'loaded' && (
        state.messages.length === 0 ? (
          <p className="workspace-status" role="status">This Thread has no messages yet.</p>
        ) : (
          <ol className="message-list">
            {state.messages.map((message, index) => (
              <li key={message.id ?? index} className={`message message--${message.role}`}>
                <span className="message-role">{message.role}</span>
                <span className="message-text">{message.text}</span>
              </li>
            ))}
          </ol>
        )
      )}
    </section>
  )
}

type WorkspaceState =
  | { phase: 'idle' }
  | { phase: 'opening' }
  | { phase: 'opened'; path: string; threads: Thread[] }
  | { phase: 'error'; message: string }

export function WorkspaceBrowser({
  open = requestWorkspace,
  openThread = requestThread,
}: { open?: typeof requestWorkspace; openThread?: typeof requestThread }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [recent, setRecent] = useState<string[]>(() => readRecentWorkspaces())
  const [state, setState] = useState<WorkspaceState>({ phase: 'idle' })
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null)

  const openWorkspace = useCallback(async (path: string, restoreThreadId?: string) => {
    const trimmed = path.trim()
    if (trimmed.length === 0) return
    setSelectedThreadId(null)
    setState({ phase: 'opening' })
    const result = await open(trimmed)
    if (result.status === 'opened') {
      setRecent(rememberWorkspace(result.path))
      setState({ phase: 'opened', path: result.path, threads: result.threads })
      const restore = restoreThreadId
        && result.threads.some((thread) => thread.id === restoreThreadId)
        ? restoreThreadId
        : undefined
      setSelectedThreadId(restore ?? null)
      rememberLastView(restore ? { workspacePath: result.path, threadId: restore } : { workspacePath: result.path })
    } else {
      setState({ phase: 'error', message: result.message })
    }
  }, [open])

  const openFromInput = useCallback(() => {
    void openWorkspace(inputRef.current?.value ?? '')
  }, [openWorkspace])

  const selectThread = useCallback((threadId: string, workspacePath: string) => {
    setSelectedThreadId(threadId)
    rememberLastView({ workspacePath, threadId })
  }, [])

  const restoredRef = useRef(false)
  useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    const last = readLastView()
    if (last.workspacePath) {
      if (inputRef.current) inputRef.current.value = last.workspacePath
      void openWorkspace(last.workspacePath, last.threadId)
    }
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
                <li key={thread.id}>
                  <button
                    type="button"
                    className={`thread-item${thread.id === selectedThreadId ? ' thread-item--selected' : ''}`}
                    aria-pressed={thread.id === selectedThreadId}
                    onClick={() => selectThread(thread.id, state.path)}
                  >
                    <span className="thread-title">{thread.title}</span>
                    <time className="thread-recency" dateTime={thread.updatedAt}>
                      {formatRecency(thread.updatedAt)}
                    </time>
                    <span className={`thread-activity thread-activity--${thread.activity}`}>
                      {thread.activity === 'live' ? 'Live' : 'Idle'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {state.phase === 'opened' && selectedThreadId !== null && (
        <ThreadView threadId={selectedThreadId} open={openThread} />
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
