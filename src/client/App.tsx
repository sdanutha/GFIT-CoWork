import { useCallback, useEffect, useRef, useState } from 'react'
import { hermesUnavailableMessage, threadErrorMessage, workspaceErrorMessage } from '../shared/contracts.js'
import type {
  ApprovalChoice,
  ApprovalDecision,
  CreateThreadResponse,
  HealthResponse,
  MessageRole,
  Thread,
  ThreadMessage,
  ThreadResponse,
  ThreadStreamEvent,
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

export async function createThread(
  cwd: string,
  fetcher: typeof fetch = fetch,
): Promise<CreateThreadResponse> {
  try {
    const response = await fetcher('/api/thread/create', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ cwd }),
    })
    if (!response.ok) return { status: 'error', message: hermesUnavailableMessage() }
    return await response.json() as CreateThreadResponse
  } catch {
    return { status: 'error', message: hermesUnavailableMessage() }
  }
}

export async function submitPrompt(
  threadId: string,
  text: string,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  try {
    await fetcher('/api/thread/prompt', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ threadId, text }),
    })
  } catch {
    // The turn still runs in Hermes; the stream reports its outcome.
  }
}

export async function stopThread(
  threadId: string,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  try {
    await fetcher('/api/thread/stop', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ threadId }),
    })
  } catch {
    // Best-effort: a failed stop leaves Hermes running the turn.
  }
}

export async function respondApproval(
  threadId: string,
  requestId: string,
  choice: ApprovalChoice,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  try {
    await fetcher('/api/thread/approval', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ threadId, requestId, choice }),
    })
  } catch {
    // A failed response leaves the approval pending in Hermes; the user can retry.
  }
}

export type StreamThread = (
  threadId: string,
  onEvent: (event: ThreadStreamEvent) => void,
) => () => void

export const streamThread: StreamThread = (threadId, onEvent) => {
  if (typeof EventSource === 'undefined') return () => {}
  const source = new EventSource(`/api/thread/stream?threadId=${encodeURIComponent(threadId)}`)
  source.addEventListener('message', (event) => {
    try {
      onEvent(JSON.parse(event.data) as ThreadStreamEvent)
    } catch {
      // Ignore malformed frames rather than breaking the live view.
    }
  })
  return () => source.close()
}

const recentWorkspacesKey = 'gfit-cowork:recent-workspaces'
const lastViewKey = 'gfit-cowork:last-view'
const themeKey = 'gfit-cowork:theme'
const draftKeyPrefix = 'gfit-cowork:draft:'
const maxRecentWorkspaces = 8

export type ThemePreference = 'system' | 'light' | 'dark'

export function readTheme(): ThemePreference {
  try {
    const stored = globalThis.localStorage?.getItem(themeKey)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

export function applyTheme(theme: ThemePreference): void {
  const root = globalThis.document?.documentElement
  if (!root) return
  if (theme === 'system') delete root.dataset.theme
  else root.dataset.theme = theme
  try {
    if (theme === 'system') globalThis.localStorage?.removeItem(themeKey)
    else globalThis.localStorage?.setItem(themeKey, theme)
  } catch {
    // Appearance is a disposable preference; ignore storage failures.
  }
}

// Drafts are the viewer's own unsent input, kept per Thread so a reload does not
// lose a half-written prompt. They are never a transcript, tool output, or secret.
export function readDraft(threadId: string): string {
  try {
    return globalThis.localStorage?.getItem(draftKeyPrefix + threadId) ?? ''
  } catch {
    return ''
  }
}

function writeDraft(threadId: string, text: string): void {
  try {
    if (text.length === 0) globalThis.localStorage?.removeItem(draftKeyPrefix + threadId)
    else globalThis.localStorage?.setItem(draftKeyPrefix + threadId, text)
  } catch {
    // Ignore storage failures; a lost draft is not worth interrupting the user.
  }
}

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

type LiveEntry =
  | { kind: 'message'; role: MessageRole; text: string; complete: boolean }
  | { kind: 'tool'; tool: string; summary?: string; details?: string; running: boolean }

function reduceLive(entries: LiveEntry[], event: ThreadStreamEvent): LiveEntry[] {
  const appendToOpenMessage = (text: string, roleForNew: MessageRole): LiveEntry[] => {
    const next = [...entries]
    for (let i = next.length - 1; i >= 0; i -= 1) {
      const entry = next[i]
      if (entry.kind === 'message' && !entry.complete) {
        next[i] = { ...entry, text: entry.text + text }
        return next
      }
    }
    return [...entries, { kind: 'message', role: roleForNew, text, complete: false }]
  }
  switch (event.kind) {
    case 'message-start':
      return [...entries, { kind: 'message', role: event.role, text: '', complete: false }]
    case 'message-delta':
      return appendToOpenMessage(event.text, 'assistant')
    case 'message-complete': {
      const next = [...entries]
      for (let i = next.length - 1; i >= 0; i -= 1) {
        const entry = next[i]
        if (entry.kind === 'message' && !entry.complete) {
          next[i] = { ...entry, complete: true }
          return next
        }
      }
      return entries
    }
    case 'tool-start':
      return [...entries, { kind: 'tool', tool: event.tool, running: true }]
    case 'tool-end': {
      const finished: LiveEntry = {
        kind: 'tool', tool: event.tool, summary: event.summary, details: event.details, running: false,
      }
      const next = [...entries]
      for (let i = next.length - 1; i >= 0; i -= 1) {
        const entry = next[i]
        if (entry.kind === 'tool' && entry.running && entry.tool === event.tool) {
          next[i] = finished
          return next
        }
      }
      return [...entries, finished]
    }
    default:
      return entries
  }
}

type ApprovalItem = { requestId: string; action: string; decision: 'pending' | ApprovalDecision }

// 'own' = a turn this view submitted; 'external' = a turn Hermes is running for
// this Thread through another surface (a Live Thread), which must not be
// interrupted or prompted over from here.
type TurnState = 'idle' | 'own' | 'external'

const approvalDecisionLabel: Record<ApprovalDecision, string> = {
  allowed: 'Allowed',
  denied: 'Denied',
  expired: 'Expired',
  failed: 'Failed',
}

export function ThreadView({
  threadId,
  initiallyLive = false,
  isNew = false,
  open = requestThread,
  subscribe = streamThread,
  submit = submitPrompt,
  stop = stopThread,
  respond = respondApproval,
}: {
  threadId: string
  initiallyLive?: boolean
  isNew?: boolean
  open?: typeof requestThread
  subscribe?: StreamThread
  submit?: typeof submitPrompt
  stop?: typeof stopThread
  respond?: typeof respondApproval
}) {
  const [state, setState] = useState<ThreadViewState>({ phase: 'loading' })
  const [live, setLive] = useState<LiveEntry[]>([])
  const [turnState, setTurnState] = useState<TurnState>(initiallyLive ? 'external' : 'idle')
  const [turnError, setTurnError] = useState<string | null>(null)
  const [approvals, setApprovals] = useState<ApprovalItem[]>([])
  const composerRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    // A just-created Thread has no stored history yet (Hermes persists it on the
    // first prompt), so open it as empty rather than reporting a read failure.
    if (isNew) {
      setState({ phase: 'loaded', messages: [] })
      return
    }
    let loading = true
    setState({ phase: 'loading' })
    void open(threadId).then((result) => {
      if (!loading) return
      if (result.status === 'opened') {
        setState({ phase: 'loaded', messages: result.messages })
        // A turn already running on open means Hermes is driving this Thread
        // elsewhere (a Live Thread) — protect the composer immediately.
        if (result.running) setTurnState('external')
      } else {
        setState({ phase: 'error', message: result.message })
      }
    })
    return () => { loading = false }
  }, [threadId, open, isNew])

  useEffect(() => {
    setLive([])
    setTurnState(initiallyLive ? 'external' : 'idle')
    setTurnError(null)
    setApprovals([])
    return subscribe(threadId, (event) => {
      if (event.kind === 'turn-start') {
        setTurnError(null)
        // A turn we did not submit belongs to another surface (Live Thread).
        setTurnState((current) => current === 'own' ? 'own' : 'external')
      }
      else if (event.kind === 'turn-end') setTurnState('idle')
      else if (event.kind === 'turn-error') { setTurnState('idle'); setTurnError(event.message) }
      else if (event.kind === 'approval-request') {
        setApprovals((items) => items.some((item) => item.requestId === event.requestId)
          ? items
          : [...items, { requestId: event.requestId, action: event.action, decision: 'pending' }])
      } else if (event.kind === 'approval-resolved') {
        setApprovals((items) => items.map((item) => item.requestId === event.requestId
          ? { ...item, decision: event.decision }
          : item))
      } else setLive((entries) => reduceLive(entries, event))
    })
  }, [threadId, subscribe, initiallyLive])

  useEffect(() => {
    const node = composerRef.current
    if (!node) return
    node.value = readDraft(threadId)
    const save = () => writeDraft(threadId, node.value)
    node.addEventListener('input', save)
    return () => node.removeEventListener('input', save)
  }, [threadId])

  const decideApproval = useCallback((requestId: string, choice: ApprovalChoice) => {
    setApprovals((items) => items.map((item) => item.requestId === requestId
      ? { ...item, decision: choice === 'allow' ? 'allowed' : 'denied' }
      : item))
    void respond(threadId, requestId, choice)
  }, [threadId, respond])

  const send = useCallback(() => {
    if (turnState !== 'idle') return
    const text = composerRef.current?.value.trim() ?? ''
    if (text.length === 0) return
    setTurnState('own')
    void submit(threadId, text)
    if (composerRef.current) composerRef.current.value = ''
    writeDraft(threadId, '')
  }, [threadId, submit, turnState])

  const externallyActive = turnState === 'external'
  const busy = turnState !== 'idle'

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
        state.messages.length === 0 && live.length === 0 ? (
          <p className="workspace-status" role="status">This Thread has no messages yet.</p>
        ) : (
          <ol className="message-list">
            {state.messages.map((message, index) => (
              <li key={message.id ?? index} className={`message message--${message.role}`}>
                <span className="message-role">{message.role}</span>
                <span className="message-text">{message.text}</span>
              </li>
            ))}
            {live.map((entry, index) => (
              entry.kind === 'message' ? (
                <li key={`live-${index}`} className={`message message--${entry.role}`}>
                  <span className="message-role">{entry.role}</span>
                  <span className="message-text">{entry.text}</span>
                </li>
              ) : (
                <li key={`live-${index}`} className="message message--tool tool-activity">
                  {entry.running ? (
                    <span className="tool-activity-summary" role="status">
                      Running {entry.tool}…
                    </span>
                  ) : entry.details ? (
                    <details className="tool-activity-details">
                      <summary>{entry.tool}: {entry.summary}</summary>
                      <pre className="tool-activity-detail-text">{entry.details}</pre>
                    </details>
                  ) : (
                    <span className="tool-activity-summary">{entry.tool}: {entry.summary}</span>
                  )}
                </li>
              )
            ))}
          </ol>
        )
      )}

      {turnError !== null && (
        <p className="workspace-status workspace-status--error" role="alert">{turnError}</p>
      )}

      {approvals.length > 0 && (
        <ul className="approval-list" aria-label="Approval requests">
          {approvals.map((approval) => (
            <li
              key={approval.requestId}
              className={`approval approval--${approval.decision}`}
              role={approval.decision === 'pending' ? 'alertdialog' : undefined}
            >
              <p className="approval-action">
                <span className="approval-label">Approval requested</span>
                {approval.action}
              </p>
              {approval.decision === 'pending' ? (
                <div className="approval-actions">
                  <button
                    type="button"
                    className="approval-deny"
                    onClick={() => decideApproval(approval.requestId, 'deny')}
                  >
                    Deny
                  </button>
                  <button
                    type="button"
                    className="approval-allow"
                    onClick={() => decideApproval(approval.requestId, 'allow')}
                  >
                    Allow
                  </button>
                </div>
              ) : (
                <p className="approval-decision">{approvalDecisionLabel[approval.decision]}</p>
              )}
            </li>
          ))}
        </ul>
      )}

      {externallyActive && (
        <p className="composer-live-note" role="status">
          Hermes is running this Thread on another surface. You can prompt once the current turn ends.
        </p>
      )}

      <form
        className="composer"
        onSubmit={(event) => { event.preventDefault(); send() }}
      >
        <label className="composer-label" htmlFor="composer-input">Message Hermes</label>
        <textarea
          id="composer-input"
          className="composer-input"
          ref={composerRef}
          rows={3}
          placeholder="Ask Hermes to do something in this Workspace…"
          defaultValue=""
          disabled={busy}
        />
        <div className="composer-actions">
          {turnState === 'own' && (
            <button
              type="button"
              className="composer-stop"
              onClick={() => { void stop(threadId); setTurnState('idle') }}
            >
              Stop
            </button>
          )}
          <button type="button" className="composer-send" onClick={send} disabled={busy}>
            {externallyActive ? 'Live elsewhere' : busy ? 'Working…' : 'Send'}
          </button>
        </div>
      </form>
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
  create = createThread,
}: {
  open?: typeof requestWorkspace
  openThread?: typeof requestThread
  create?: typeof createThread
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [recent, setRecent] = useState<string[]>(() => readRecentWorkspaces())
  const [state, setState] = useState<WorkspaceState>({ phase: 'idle' })
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const createdThreadIds = useRef<Set<string>>(new Set())

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

  const createInWorkspace = useCallback(async (workspacePath: string) => {
    setCreateError(null)
    const result = await create(workspacePath)
    if (result.status !== 'created') {
      setCreateError(result.message)
      return
    }
    const thread: Thread = {
      id: result.threadId,
      title: 'New Thread',
      updatedAt: new Date().toISOString(),
      activity: 'idle',
    }
    createdThreadIds.current.add(thread.id)
    setState((current) => current.phase === 'opened' && current.path === workspacePath
      ? { ...current, threads: [thread, ...current.threads.filter((t) => t.id !== thread.id)] }
      : current)
    setSelectedThreadId(thread.id)
    rememberLastView({ workspacePath, threadId: thread.id })
  }, [create])

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
          <div className="thread-list-header">
            <p className="thread-list-heading">
              Threads in <span className="workspace-path-name">{state.path}</span>
            </p>
            <button
              type="button"
              className="thread-new-button"
              onClick={() => { void createInWorkspace(state.path) }}
            >
              New Thread
            </button>
          </div>
          {createError !== null && (
            <p className="workspace-status workspace-status--error" role="alert">{createError}</p>
          )}
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
        <ThreadView
          threadId={selectedThreadId}
          initiallyLive={state.threads.some((t) => t.id === selectedThreadId && t.activity === 'live')}
          isNew={createdThreadIds.current.has(selectedThreadId)}
          open={openThread}
        />
      )}
    </section>
  )
}

type AppProps = {
  health?: HealthResponse
  onRetry?: () => void
}

const themeOrder: ThemePreference[] = ['system', 'light', 'dark']
const themeLabel: Record<ThemePreference, string> = {
  system: 'System',
  light: 'Light',
  dark: 'Dark',
}

export function App({ health, onRetry }: AppProps) {
  const [currentHealth, setCurrentHealth] = useState<HealthResponse | null>(health ?? null)
  const [theme, setTheme] = useState<ThemePreference>(() => readTheme())
  const loadHealth = useCallback(async () => {
    setCurrentHealth(null)
    setCurrentHealth(await requestHealth())
  }, [])

  useEffect(() => {
    if (health === undefined) void loadHealth()
  }, [health, loadHealth])

  useEffect(() => { applyTheme(theme) }, [theme])

  const retry = onRetry ?? (() => { void loadHealth() })

  return (
    <main className="app-shell">
      <article className="status-card" aria-labelledby="product-name">
        <div className="card-top">
          <div className="brand-mark" aria-hidden="true">G</div>
          <button
            className="theme-toggle"
            type="button"
            onClick={() => setTheme(themeOrder[(themeOrder.indexOf(theme) + 1) % themeOrder.length])}
            aria-label={`Appearance: ${themeLabel[theme]}. Click to change.`}
          >
            {themeLabel[theme]}
          </button>
        </div>
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
