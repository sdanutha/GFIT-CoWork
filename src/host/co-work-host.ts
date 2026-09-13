import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import {
  createThreadResponse,
  healthResponse,
  hermesUnavailableMessage,
  threadErrorMessage,
  threadResponse,
  workspaceErrorMessage,
  workspaceResponse,
} from '../shared/contracts.js'
import type {
  CreateThreadResponse,
  ThreadResponse,
  WorkspaceResponse,
} from '../shared/contracts.js'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'

const safeUnavailableRemedy = 'Check your Hermes setup, then retry.'
const maxRequestBodyBytes = 8 * 1024

const readJsonBody = async (request: IncomingMessage): Promise<Record<string, unknown>> => {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.length
    if (size > maxRequestBodyBytes) return {}
    chunks.push(chunk as Buffer)
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    return typeof parsed === 'object' && parsed !== null ? parsed as Record<string, unknown> : {}
  } catch {
    return {}
  }
}

const stringField = (body: Record<string, unknown>, field: string): string =>
  typeof body[field] === 'string' ? body[field] as string : ''

export function createCoWorkHost(gateway: HermesWorkspaceGateway) {
  let closeOperation: Promise<void> | undefined

  const sendJson = (response: ServerResponse, body: unknown) => {
    response.writeHead(200, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    })
    response.end(JSON.stringify(body))
  }

  const server = createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/api/health') {
      let readiness
      try {
        readiness = await gateway.health()
      } catch {
        readiness = { kind: 'unavailable' as const, remedy: safeUnavailableRemedy }
      }
      sendJson(response, healthResponse(
        readiness.kind === 'ready'
          ? readiness
          : { kind: 'unavailable', remedy: safeUnavailableRemedy },
      ))
      return
    }

    if (request.method === 'POST' && request.url === '/api/workspace') {
      const path = stringField(await readJsonBody(request), 'path')
      try {
        sendJson(response, workspaceResponse(await gateway.openWorkspace(path)))
      } catch {
        // Never surface a raw gateway failure (connection details, credentials)
        // to the browser; report a safe, actionable unavailable state instead.
        sendJson(response, {
          status: 'error',
          reason: 'unavailable',
          message: workspaceErrorMessage('unavailable'),
        } satisfies WorkspaceResponse)
      }
      return
    }

    if (request.method === 'POST' && request.url === '/api/thread') {
      const threadId = stringField(await readJsonBody(request), 'threadId')
      try {
        sendJson(response, threadResponse(await gateway.openThread(threadId)))
      } catch {
        sendJson(response, {
          status: 'error',
          reason: 'unavailable',
          message: threadErrorMessage('unavailable'),
        } satisfies ThreadResponse)
      }
      return
    }

    if (request.method === 'POST' && request.url === '/api/thread/create') {
      const body = await readJsonBody(request)
      const cwd = stringField(body, 'cwd')
      const title = stringField(body, 'title')
      try {
        sendJson(response, createThreadResponse(
          await gateway.createThread(cwd, title.length > 0 ? title : undefined),
        ))
      } catch {
        sendJson(response, {
          status: 'error',
          message: hermesUnavailableMessage(),
        } satisfies CreateThreadResponse)
      }
      return
    }

    if (request.method === 'POST' && request.url === '/api/thread/prompt') {
      const body = await readJsonBody(request)
      try {
        await gateway.submitPrompt(stringField(body, 'threadId'), stringField(body, 'text'))
        sendJson(response, { status: 'submitted' })
      } catch {
        sendJson(response, { status: 'error', message: hermesUnavailableMessage() })
      }
      return
    }

    if (request.method === 'POST' && request.url === '/api/thread/stop') {
      try {
        await gateway.stopThread(stringField(await readJsonBody(request), 'threadId'))
        sendJson(response, { status: 'stopped' })
      } catch {
        sendJson(response, { status: 'error', message: hermesUnavailableMessage() })
      }
      return
    }

    if (request.method === 'POST' && request.url === '/api/thread/approval') {
      const body = await readJsonBody(request)
      const choice = stringField(body, 'choice') === 'allow' ? 'allow' : 'deny'
      try {
        await gateway.respondApproval(stringField(body, 'threadId'), stringField(body, 'requestId'), choice)
        sendJson(response, { status: 'resolved', choice })
      } catch {
        sendJson(response, { status: 'error', message: hermesUnavailableMessage() })
      }
      return
    }

    if (request.method === 'GET' && request.url?.startsWith('/api/thread/stream')) {
      const threadId = new URL(request.url, 'http://127.0.0.1').searchParams.get('threadId') ?? ''
      response.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-store',
        connection: 'keep-alive',
      })
      // Hermes owns the turn; the host only forwards its events to the browser.
      const unsubscribe = gateway.subscribe(threadId, (event) => {
        response.write(`data: ${JSON.stringify(event)}\n\n`)
      })
      const stop = () => { unsubscribe(); response.end() }
      request.on('close', stop)
      request.on('error', stop)
      return
    }

    response.writeHead(404).end()
  })

  return {
    listen: (port: number) => new Promise<AddressInfo>((resolve) => {
      server.listen(port, '127.0.0.1', () => resolve(server.address() as AddressInfo))
    }),
    close: () => closeOperation ??= (async () => {
      try {
        if (server.listening) {
          await new Promise<void>((resolve, reject) => server.close((error) => {
            if (error) reject(error)
            else resolve()
          }))
        }
      } finally {
        await gateway.close()
      }
    })(),
  }
}
