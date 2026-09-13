import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import { healthResponse, workspaceErrorMessage, workspaceResponse } from '../shared/contracts.js'
import type { WorkspaceResponse } from '../shared/contracts.js'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'

const safeUnavailableRemedy = 'Check your Hermes setup, then retry.'
const maxRequestBodyBytes = 8 * 1024

const readRequestedPath = async (request: IncomingMessage): Promise<string> => {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.length
    if (size > maxRequestBodyBytes) return ''
    chunks.push(chunk as Buffer)
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8')) as { path?: unknown }
    return typeof parsed.path === 'string' ? parsed.path : ''
  } catch {
    return ''
  }
}

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
      const path = await readRequestedPath(request)
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
