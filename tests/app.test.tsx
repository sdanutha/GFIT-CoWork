import assert from 'node:assert/strict'
import test from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { App, requestHealth } from '../src/client/App.js'

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
    JSON.stringify({ remedy: 'password=do-not-render' }),
    { status: 503 },
  )

  const health = await requestHealth(fetcher)

  assert.deepEqual(health, {
    status: 'unavailable',
    runtime: 'Hermes',
    remedy: 'Check your Hermes setup, then retry.',
  })
})

test('announces that Hermes readiness is being checked on launch', () => {
  const html = renderToStaticMarkup(<App />)

  assert.match(html, /Checking Hermes/)
  assert.match(html, /role="status"/)
})
