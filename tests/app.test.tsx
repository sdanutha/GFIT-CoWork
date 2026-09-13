import assert from 'node:assert/strict'
import test from 'node:test'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { JSDOM } from 'jsdom'
import { App, requestHealth } from '../src/client/App.js'
import type { ThreadStreamEvent } from '../src/shared/contracts.js'
import { withDom, typeInto } from './support/dom.js'

test('shows GFIT CoWork ready for Hermes', () => {
  const html = renderToStaticMarkup(
    <App health={{ status: 'ready', runtime: 'Hermes', startedByCoWork: false }} />,
  )

  assert.match(html, /GFIT CoWork/)
  assert.match(html, /Hermes is ready/)
  assert.doesNotMatch(html, /Claude/)
})

test('shows safe Hermes setup guidance and a retry action when unavailable', () => {
  const html = renderToStaticMarkup(
    <App
      health={{
        status: 'unavailable',
        runtime: 'Hermes',
        remedy: 'Start Hermes with hermes serve, then retry.',
      }}
    />,
  )

  assert.match(html, /Hermes is unavailable/)
  assert.match(html, /Start Hermes with hermes serve, then retry\./)
  assert.match(html, /<button[^>]*>Retry<\/button>/)
  assert.doesNotMatch(html, /Claude/)
})

test('loads readiness only from the CoWork health endpoint', async () => {
  const requested: Array<string | URL | Request> = []
  const fetcher: typeof fetch = async (input) => {
    requested.push(input)
    return new Response(JSON.stringify({
      status: 'ready',
      runtime: 'Hermes',
      startedByCoWork: true,
    }), { status: 200 })
  }

  assert.deepEqual(await requestHealth(fetcher), {
    status: 'ready',
    runtime: 'Hermes',
    startedByCoWork: true,
  })
  assert.deepEqual(requested, ['/api/health'])
})

test('turns a failed health request into safe Hermes guidance', async () => {
  const fetcher: typeof fetch = async () => {
    throw new Error('token=do-not-render')
  }

  const health = await requestHealth(fetcher)

  assert.deepEqual(health, {
    status: 'unavailable',
    runtime: 'Hermes',
    remedy: 'Check your Hermes setup, then retry.',
  })
  assert.doesNotMatch(JSON.stringify(health), /do-not-render/)
})

test('does not render an unsuccessful health response body', async () => {
  const fetcher: typeof fetch = async () => new Response(
    JSON.stringify({
      remedy: 'Gateway http://127.0.0.1:9119 failed with token=do-not-render and password=secret.',
    }),
    { status: 503 },
  )

  const health = await requestHealth(fetcher)

  assert.deepEqual(health, {
    status: 'unavailable',
    runtime: 'Hermes',
    remedy: 'Check your Hermes setup, then retry.',
  })
  assert.doesNotMatch(
    JSON.stringify(health),
    /token|key|password|127\.0\.0\.1:9119|do-not-render|secret/i,
  )
})

test('announces that Hermes readiness is being checked on launch', () => {
  const html = renderToStaticMarkup(<App />)

  assert.match(html, /Checking Hermes/)
  assert.match(html, /role="status"/)
})

test('loads Hermes health on mount and retries from the unavailable screen', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://127.0.0.1/' })
  const browserGlobals = ['window', 'document', 'HTMLElement', 'Node', 'Event', 'MouseEvent'] as const
  const originalDescriptors = new Map(
    browserGlobals.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]),
  )
  const originalFetch = globalThis.fetch
  const actEnvironment = globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean
  }
  const originalActEnvironment = actEnvironment.IS_REACT_ACT_ENVIRONMENT
  let root: Root | undefined

  Object.defineProperties(globalThis, {
    window: { configurable: true, writable: true, value: dom.window },
    document: { configurable: true, writable: true, value: dom.window.document },
    HTMLElement: { configurable: true, writable: true, value: dom.window.HTMLElement },
    Node: { configurable: true, writable: true, value: dom.window.Node },
    Event: { configurable: true, writable: true, value: dom.window.Event },
    MouseEvent: { configurable: true, writable: true, value: dom.window.MouseEvent },
  })
  actEnvironment.IS_REACT_ACT_ENVIRONMENT = true

  try {
    const requests: string[] = []
    const healthResponses = [
      {
        status: 'unavailable',
        runtime: 'Hermes',
        remedy: 'Run hermes setup.',
      },
      {
        status: 'ready',
        runtime: 'Hermes',
        startedByCoWork: false,
      },
    ]
    globalThis.fetch = async (input) => {
      requests.push(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
      return new Response(JSON.stringify(healthResponses[requests.length - 1]), { status: 200 })
    }

    const container = dom.window.document.querySelector('#root')
    assert.ok(container)

    await act(async () => {
      root = createRoot(container)
      root.render(<App />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    assert.deepEqual(requests, ['/api/health'])
    assert.match(container.textContent ?? '', /Hermes is unavailable/)
    const retryButton = container.querySelector('.retry-button')
    assert.ok(retryButton)

    await act(async () => {
      retryButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    assert.deepEqual(requests, ['/api/health', '/api/health'])
    assert.match(container.textContent ?? '', /Hermes is ready/)
  } finally {
    if (root !== undefined) {
      await act(async () => { root?.unmount() })
    }
    globalThis.fetch = originalFetch
    if (originalActEnvironment === undefined) delete actEnvironment.IS_REACT_ACT_ENVIRONMENT
    else actEnvironment.IS_REACT_ACT_ENVIRONMENT = originalActEnvironment
    for (const name of browserGlobals) {
      const descriptor = originalDescriptors.get(name)
      if (descriptor === undefined) delete (globalThis as Record<string, unknown>)[name]
      else Object.defineProperty(globalThis, name, descriptor)
    }
    dom.window.close()
  }
})

test('requests a Workspace from the CoWork host and returns its scoped Threads', async () => {
  const calls: Array<{ url: string; body: unknown }> = []
  const fetcher: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), body: JSON.parse(String(init?.body ?? '{}')) })
    return new Response(JSON.stringify({
      status: 'opened',
      path: '/home/dev/project',
      threads: [],
    }), { status: 200 })
  }

  const { requestWorkspace } = await import('../src/client/App.js')
  const result = await requestWorkspace('/home/dev/project', fetcher)

  assert.deepEqual(result, { status: 'opened', path: '/home/dev/project', threads: [] })
  assert.deepEqual(calls, [{ url: '/api/workspace', body: { path: '/home/dev/project' } }])
})

test('turns a failed Workspace request into a safe, non-leaking state', async () => {
  const fetcher: typeof fetch = async () => { throw new Error('token=do-not-render') }
  const { requestWorkspace } = await import('../src/client/App.js')

  const result = await requestWorkspace('/home/dev/project', fetcher)

  assert.equal(result.status, 'error')
  assert.doesNotMatch(JSON.stringify(result), /token|do-not-render/i)
})

test('opens a Workspace and lists its Threads with title, recency, and activity', async () => {
  await withDom(async (dom) => {
    const { WorkspaceBrowser } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestWorkspace = async (path) => ({
      status: 'opened',
      path,
      threads: [
        { id: 't1', title: 'Refactor gateway', updatedAt: '2026-09-13T08:30:00.000Z', activity: 'live' },
        { id: 't2', title: 'Write the spec', updatedAt: '2026-09-10T00:00:00.000Z', activity: 'idle' },
      ],
    })
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined

    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<WorkspaceBrowser open={open} />)
        await Promise.resolve()
      })
      const input = container.querySelector('#workspace-path') as HTMLInputElement
      const openButton = container.querySelector('.workspace-open-button')
      assert.ok(openButton)
      typeInto(dom, input, '/home/dev/project')
      await act(async () => {
        openButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      const text = container.textContent ?? ''
      assert.match(text, /Refactor gateway/)
      assert.match(text, /Write the spec/)
      assert.match(text, /Live/)
      assert.match(text, /Idle/)
      assert.match(text, /Sep 13, 2026/)
      assert.match(text, /\/home\/dev\/project/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('shows a safe error message when a Workspace cannot be opened', async () => {
  await withDom(async (dom) => {
    const { WorkspaceBrowser } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestWorkspace = async () => ({
      status: 'error',
      reason: 'not-found',
      message: 'That folder does not exist on this machine.',
    })
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined

    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<WorkspaceBrowser open={open} />)
        await Promise.resolve()
      })
      const input = container.querySelector('#workspace-path') as HTMLInputElement
      const openButton = container.querySelector('.workspace-open-button')
      assert.ok(openButton)
      typeInto(dom, input, '/missing')
      await act(async () => {
        openButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      const alert = container.querySelector('[role="alert"]')
      assert.ok(alert)
      assert.match(alert.textContent ?? '', /does not exist/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('remembers opened Workspaces and reopens a recent one', async () => {
  await withDom(async (dom) => {
    const { WorkspaceBrowser, readRecentWorkspaces } = await import('../src/client/App.js')
    let opened = 0
    const open: typeof import('../src/client/App.js').requestWorkspace = async (path) => {
      opened += 1
      return { status: 'opened', path, threads: [] }
    }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined

    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<WorkspaceBrowser open={open} />)
        await Promise.resolve()
      })
      const input = container.querySelector('#workspace-path') as HTMLInputElement
      const openButton = container.querySelector('.workspace-open-button')
      assert.ok(openButton)
      typeInto(dom, input, '/home/dev/remembered')
      await act(async () => {
        openButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      assert.deepEqual(readRecentWorkspaces(), ['/home/dev/remembered'])
      const recentButton = [...container.querySelectorAll('.workspace-recent-item')]
        .find((element) => element.textContent === '/home/dev/remembered')
      assert.ok(recentButton)

      await act(async () => {
        recentButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      assert.equal(opened, 2)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('requests a Thread from the CoWork host and returns its messages', async () => {
  const calls: Array<{ url: string; body: unknown }> = []
  const fetcher: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), body: JSON.parse(String(init?.body ?? '{}')) })
    return new Response(JSON.stringify({
      status: 'opened',
      threadId: 's1',
      messages: [{ role: 'user', text: 'hi' }],
    }), { status: 200 })
  }
  const { requestThread } = await import('../src/client/App.js')
  const result = await requestThread('s1', fetcher)

  assert.deepEqual(result, { status: 'opened', threadId: 's1', messages: [{ role: 'user', text: 'hi' }] })
  assert.deepEqual(calls, [{ url: '/api/thread', body: { threadId: 's1' } }])
})

test('turns a failed Thread request into a safe, non-leaking state', async () => {
  const fetcher: typeof fetch = async () => { throw new Error('token=do-not-render') }
  const { requestThread } = await import('../src/client/App.js')
  const result = await requestThread('s1', fetcher)

  assert.equal(result.status, 'error')
  assert.doesNotMatch(JSON.stringify(result), /token|do-not-render/i)
})

test('renders a selected Thread history in message order from Hermes', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened',
      threadId,
      messages: [
        { role: 'user', text: 'Please refactor' },
        { role: 'assistant', text: 'Working on it' },
        { role: 'tool', text: 'ran the tests' },
      ],
    })
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="s1" open={open} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const text = container.textContent ?? ''
      assert.match(text, /Please refactor/)
      assert.match(text, /Working on it/)
      assert.match(text, /ran the tests/)
      const items = [...container.querySelectorAll('.message-text')].map((n) => n.textContent)
      assert.deepEqual(items, ['Please refactor', 'Working on it', 'ran the tests'])
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('shows safe recovery guidance when a Thread cannot be read', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestThread = async () => ({
      status: 'error',
      reason: 'not-found',
      message: 'That Thread no longer exists in Hermes.',
    })
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="gone" open={open} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const alert = container.querySelector('[role="alert"]')
      assert.ok(alert)
      assert.match(alert.textContent ?? '', /no longer exists/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('restores the last Workspace and selected Thread on return', async () => {
  await withDom(async (dom) => {
    const { WorkspaceBrowser } = await import('../src/client/App.js')
    dom.window.localStorage.setItem(
      'gfit-cowork:last-view',
      JSON.stringify({ workspacePath: '/home/dev/project', threadId: 't1' }),
    )
    const open: typeof import('../src/client/App.js').requestWorkspace = async (path) => ({
      status: 'opened',
      path,
      threads: [{ id: 't1', title: 'Remembered thread', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' }],
    })
    const openThread: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened',
      threadId,
      messages: [{ role: 'assistant', text: 'restored message' }],
    })
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<WorkspaceBrowser open={open} openThread={openThread} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const text = container.textContent ?? ''
      assert.match(text, /\/home\/dev\/project/)
      assert.match(text, /Remembered thread/)
      assert.match(text, /restored message/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('opens a Thread when its list item is selected', async () => {
  await withDom(async (dom) => {
    const { WorkspaceBrowser } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestWorkspace = async (path) => ({
      status: 'opened',
      path,
      threads: [{ id: 't9', title: 'Pick me', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' }],
    })
    let openedThreadId = ''
    const openThread: typeof import('../src/client/App.js').requestThread = async (threadId) => {
      openedThreadId = threadId
      return { status: 'opened', threadId, messages: [{ role: 'user', text: 'hello thread' }] }
    }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<WorkspaceBrowser open={open} openThread={openThread} />)
        await Promise.resolve()
      })
      const input = container.querySelector('#workspace-path') as HTMLInputElement
      const openButton = container.querySelector('.workspace-open-button')
      assert.ok(openButton)
      typeInto(dom, input, '/home/dev/project')
      await act(async () => {
        openButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const threadButton = container.querySelector('.thread-item')
      assert.ok(threadButton)
      await act(async () => {
        threadButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      assert.equal(openedThreadId, 't9')
      assert.match(container.textContent ?? '', /hello thread/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('streams assistant text and tool activity into the selected Thread', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened', threadId, messages: [],
    })
    const subscribe: import('../src/client/App.js').StreamThread = (_threadId, onEvent) => {
      queueMicrotask(() => {
        onEvent({ kind: 'turn-start' })
        onEvent({ kind: 'message-start', role: 'assistant' })
        onEvent({ kind: 'message-delta', text: 'Hel' })
        onEvent({ kind: 'message-delta', text: 'lo' })
        onEvent({ kind: 'tool-start', tool: 'shell' })
        onEvent({ kind: 'tool-end', tool: 'shell', summary: 'ran ls', details: 'file-a\nfile-b' })
        onEvent({ kind: 'message-complete' })
        onEvent({ kind: 'turn-end' })
      })
      return () => {}
    }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="s1" open={open} subscribe={subscribe} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const text = container.textContent ?? ''
      assert.match(text, /Hello/)
      assert.match(text, /shell: ran ls/)
      const details = container.querySelector('.tool-activity-details')
      assert.ok(details)
      assert.match(details.textContent ?? '', /file-a/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('submits a prompt from the composer and can stop the active turn', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened', threadId, messages: [],
    })
    const submitted: Array<{ threadId: string; text: string }> = []
    let stoppedThread = ''
    const submit: typeof import('../src/client/App.js').submitPrompt = async (threadId, text) => {
      submitted.push({ threadId, text })
    }
    const stop: typeof import('../src/client/App.js').stopThread = async (threadId) => {
      stoppedThread = threadId
    }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="s7" open={open} subscribe={() => () => {}} submit={submit} stop={stop} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const composer = container.querySelector('#composer-input') as HTMLTextAreaElement
      composer.value = 'Refactor the gateway'
      const sendButton = container.querySelector('.composer-send')
      assert.ok(sendButton)
      await act(async () => {
        sendButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      assert.deepEqual(submitted, [{ threadId: 's7', text: 'Refactor the gateway' }])

      const stopButton = container.querySelector('.composer-stop')
      assert.ok(stopButton)
      await act(async () => {
        stopButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      assert.equal(stoppedThread, 's7')
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('creates a new Thread in the Workspace and selects it', async () => {
  await withDom(async (dom) => {
    const { WorkspaceBrowser } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestWorkspace = async (path) => ({
      status: 'opened', path, threads: [],
    })
    // A brand-new Thread has no stored history yet, so opening it would fail;
    // the new-Thread view must not attempt the read or show that error.
    const openThread: typeof import('../src/client/App.js').requestThread = async () => ({
      status: 'error', reason: 'unreadable', message: 'That Thread could not be read.',
    })
    let createdCwd = ''
    const create: typeof import('../src/client/App.js').createThread = async (cwd) => {
      createdCwd = cwd
      return { status: 'created', threadId: 'nt1' }
    }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<WorkspaceBrowser open={open} openThread={openThread} create={create} />)
        await Promise.resolve()
      })
      const input = container.querySelector('#workspace-path') as HTMLInputElement
      typeInto(dom, input, '/home/dev/project')
      await act(async () => {
        container.querySelector('.workspace-open-button')
          ?.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      await act(async () => {
        container.querySelector('.thread-new-button')
          ?.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      assert.equal(createdCwd, '/home/dev/project')
      assert.match(container.textContent ?? '', /New Thread/)
      const threadView = container.querySelector('.thread-view')
      assert.ok(threadView)
      // Empty, promptable state — not the "could not be read" error.
      assert.match(threadView.textContent ?? '', /no messages yet/)
      assert.doesNotMatch(threadView.textContent ?? '', /could not be read/)
      assert.equal(threadView.querySelector('[role="alert"]'), null)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('presents Approval requests and resolves them by explicit choice', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened', threadId, messages: [],
    })
    let emit: ((event: ThreadStreamEvent) => void) | undefined
    const subscribe: import('../src/client/App.js').StreamThread = (_threadId, onEvent) => {
      emit = onEvent
      return () => {}
    }
    const decisions: Array<{ requestId: string; choice: string }> = []
    const respond: typeof import('../src/client/App.js').respondApproval = async (_t, requestId, choice) => {
      decisions.push({ requestId, choice })
    }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="s1" open={open} subscribe={subscribe} submit={async () => {}} respond={respond} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      assert.ok(emit)
      await act(async () => {
        emit?.({ kind: 'approval-request', requestId: 'a1', action: 'run rm -rf build' })
        await Promise.resolve()
      })
      assert.match(container.textContent ?? '', /run rm -rf build/)
      const allowButton = container.querySelector('.approval-allow')
      const denyButton = container.querySelector('.approval-deny')
      assert.ok(allowButton)
      assert.ok(denyButton)

      await act(async () => {
        allowButton.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      assert.deepEqual(decisions, [{ requestId: 'a1', choice: 'allow' }])
      assert.match(container.textContent ?? '', /Allowed/)

      await act(async () => {
        emit?.({ kind: 'approval-request', requestId: 'a2', action: 'delete file' })
        emit?.({ kind: 'approval-resolved', requestId: 'a2', decision: 'expired' })
        await Promise.resolve()
      })
      assert.match(container.textContent ?? '', /Expired/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('blocks the composer for an externally Live Thread and re-enables it when the turn ends', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened', threadId, messages: [],
    })
    let emit: ((event: ThreadStreamEvent) => void) | undefined
    const subscribe: import('../src/client/App.js').StreamThread = (_threadId, onEvent) => {
      emit = onEvent
      return () => {}
    }
    const submitted: string[] = []
    const submit: typeof import('../src/client/App.js').submitPrompt = async (_t, text) => { submitted.push(text) }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="s1" initiallyLive open={open} subscribe={subscribe} submit={submit} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const composer = () => container.querySelector('#composer-input') as HTMLTextAreaElement
      const sendButton = () => container.querySelector('.composer-send') as HTMLButtonElement

      // Live from the start: composer disabled, explains why, no Stop (not our turn).
      assert.equal(composer().disabled, true)
      assert.equal(sendButton().disabled, true)
      assert.match(container.textContent ?? '', /another surface/)
      assert.equal(container.querySelector('.composer-stop'), null)

      // External turn ends -> promptable again, no stale live state.
      await act(async () => { emit?.({ kind: 'turn-end' }); await Promise.resolve() })
      assert.equal(composer().disabled, false)
      assert.doesNotMatch(container.textContent ?? '', /another surface/)

      composer().value = 'now I can prompt'
      await act(async () => {
        sendButton().dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      assert.deepEqual(submitted, ['now I can prompt'])
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('treats a stream turn we did not start as external and shows a gateway turn error', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    const open: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened', threadId, messages: [],
    })
    let emit: ((event: ThreadStreamEvent) => void) | undefined
    const subscribe: import('../src/client/App.js').StreamThread = (_threadId, onEvent) => { emit = onEvent; return () => {} }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="s1" open={open} subscribe={subscribe} submit={async () => {}} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const composer = () => container.querySelector('#composer-input') as HTMLTextAreaElement
      assert.equal(composer().disabled, false)

      await act(async () => { emit?.({ kind: 'turn-start' }); await Promise.resolve() })
      assert.equal(composer().disabled, true)
      assert.match(container.textContent ?? '', /another surface/)

      await act(async () => { emit?.({ kind: 'turn-error', message: 'The Hermes turn ended with an error.' }); await Promise.resolve() })
      assert.equal(composer().disabled, false)
      assert.match(container.textContent ?? '', /ended with an error/)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('persists an explicit dark-mode choice and follows system by default', async () => {
  await withDom(async (dom) => {
    const { applyTheme, readTheme } = await import('../src/client/App.js')
    const root = dom.window.document.documentElement

    assert.equal(readTheme(), 'system')
    applyTheme('dark')
    assert.equal(root.dataset.theme, 'dark')
    assert.equal(readTheme(), 'dark')

    applyTheme('light')
    assert.equal(root.dataset.theme, 'light')
    assert.equal(readTheme(), 'light')

    applyTheme('system')
    assert.equal(root.dataset.theme, undefined)
    assert.equal(readTheme(), 'system')
  })
})

test('cycles the appearance toggle and stores the choice', async () => {
  await withDom(async (dom) => {
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<App health={{ status: 'ready', runtime: 'Hermes', startedByCoWork: false }} />)
        await Promise.resolve()
      })
      const toggle = container.querySelector('.theme-toggle') as HTMLButtonElement
      assert.match(toggle.textContent ?? '', /System/)
      await act(async () => {
        toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      assert.match(toggle.textContent ?? '', /Light/)
      assert.equal(dom.window.localStorage.getItem('gfit-cowork:theme'), 'light')
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('restores a per-Thread draft and clears it after sending', async () => {
  await withDom(async (dom) => {
    const { ThreadView } = await import('../src/client/App.js')
    dom.window.localStorage.setItem('gfit-cowork:draft:s1', 'a half-written prompt')
    const open: typeof import('../src/client/App.js').requestThread = async (threadId) => ({
      status: 'opened', threadId, messages: [],
    })
    const submitted: string[] = []
    const submit: typeof import('../src/client/App.js').submitPrompt = async (_t, text) => { submitted.push(text) }
    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<ThreadView threadId="s1" open={open} subscribe={() => () => {}} submit={submit} />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      const composer = container.querySelector('#composer-input') as HTMLTextAreaElement
      assert.equal(composer.value, 'a half-written prompt')

      await act(async () => {
        container.querySelector('.composer-send')?.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      assert.deepEqual(submitted, ['a half-written prompt'])
      assert.equal(composer.value, '')
      assert.equal(dom.window.localStorage.getItem('gfit-cowork:draft:s1'), null)
    } finally {
      if (root) await act(async () => { root?.unmount() })
    }
  })
})

test('end-to-end: Workspace to Thread to prompt to approval, leaking nothing to preferences', async () => {
  await withDom(async (dom) => {
    const originalFetch = globalThis.fetch
    const originalEventSource = (globalThis as { EventSource?: unknown }).EventSource

    const sources: Array<{ url: string; emit: (event: ThreadStreamEvent) => void }> = []
    class MockEventSource {
      listeners: Array<(e: { data: string }) => void> = []
      constructor(public url: string) {
        sources.push({ url, emit: (event) => this.listeners.forEach((cb) => cb({ data: JSON.stringify(event) })) })
      }
      addEventListener(type: string, cb: (e: { data: string }) => void) {
        if (type === 'message') this.listeners.push(cb)
      }
      close() {}
    }

    const calls: Array<{ url: string; body: Record<string, unknown> }> = []
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {}
      calls.push({ url, body })
      const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 })
      if (url === '/api/health') return json({ status: 'ready', runtime: 'Hermes', startedByCoWork: false })
      if (url === '/api/workspace') return json({ status: 'opened', path: body.path, threads: [
        { id: 't1', title: 'Existing thread', updatedAt: '2026-09-13T00:00:00.000Z', activity: 'idle' },
      ] })
      if (url === '/api/thread') return json({ status: 'opened', threadId: body.threadId, messages: [
        { role: 'user', text: 'earlier question' },
        { role: 'assistant', text: 'earlier reply' },
      ] })
      if (url === '/api/thread/prompt') return json({ status: 'submitted' })
      if (url === '/api/thread/approval') return json({ status: 'resolved', choice: body.choice })
      return new Response('{}', { status: 404 })
    }) as typeof fetch
    ;(globalThis as { EventSource?: unknown }).EventSource = MockEventSource as unknown

    const container = dom.window.document.querySelector('#root')
    assert.ok(container)
    let root: Root | undefined
    try {
      await act(async () => {
        root = createRoot(container)
        root.render(<App />)
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      assert.match(container.textContent ?? '', /Hermes is ready/)

      // Open the fixture Workspace.
      const input = container.querySelector('#workspace-path') as HTMLInputElement
      typeInto(dom, input, '/home/dev/project')
      await act(async () => {
        container.querySelector('.workspace-open-button')?.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      assert.match(container.textContent ?? '', /Existing thread/)

      // Select the Thread and read its history.
      await act(async () => {
        container.querySelector('.thread-item')?.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      assert.match(container.textContent ?? '', /earlier reply/)
      assert.equal(sources.length, 1)

      // Prompt, then stream a turn that asks for approval.
      const composer = container.querySelector('#composer-input') as HTMLTextAreaElement
      composer.value = 'run the migration'
      await act(async () => {
        container.querySelector('.composer-send')?.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      await act(async () => {
        sources[0].emit({ kind: 'turn-start' })
        sources[0].emit({ kind: 'message-start', role: 'assistant' })
        sources[0].emit({ kind: 'message-delta', text: 'Running the plan' })
        sources[0].emit({ kind: 'approval-request', requestId: 'ap1', action: 'execute migration script' })
        await Promise.resolve()
      })
      assert.match(container.textContent ?? '', /Running the plan/)
      assert.match(container.textContent ?? '', /execute migration script/)

      // Allow the approval, then finish the turn.
      await act(async () => {
        container.querySelector('.approval-allow')?.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
        await Promise.resolve()
      })
      await act(async () => {
        sources[0].emit({ kind: 'approval-resolved', requestId: 'ap1', decision: 'allowed' })
        sources[0].emit({ kind: 'message-complete' })
        sources[0].emit({ kind: 'turn-end' })
        await Promise.resolve()
      })
      assert.match(container.textContent ?? '', /Allowed/)

      // The whole journey hit the expected endpoints, defaulting to no full access.
      const urls = calls.map((c) => c.url)
      assert.ok(urls.includes('/api/thread/prompt'))
      const approvalCall = calls.find((c) => c.url === '/api/thread/approval')
      assert.equal(approvalCall?.body.choice, 'allow')

      // Preference storage holds navigation only — no transcript, tool output, or approval secret.
      const stored = Object.keys(dom.window.localStorage)
        .map((key) => `${key}=${dom.window.localStorage.getItem(key)}`)
        .join('\n')
      assert.doesNotMatch(stored, /earlier reply|Running the plan|execute migration script|earlier question/)
    } finally {
      globalThis.fetch = originalFetch
      ;(globalThis as { EventSource?: unknown }).EventSource = originalEventSource
      if (root) await act(async () => { root?.unmount() })
    }
  })
})
