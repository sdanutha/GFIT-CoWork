import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { healthResponse } from '../shared/contracts.js'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'

const safeUnavailableRemedy = 'Check your Hermes setup, then retry.'

export function createCoWorkHost(gateway: HermesWorkspaceGateway) {
  let closeOperation: Promise<void> | undefined
  const server = createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/api/health') {
      const readiness = await gateway.health()
      const body = JSON.stringify(healthResponse(
        readiness.kind === 'ready'
          ? readiness
          : { kind: 'unavailable', remedy: safeUnavailableRemedy },
      ))
      response.writeHead(200, {
        'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store',
      })
      response.end(body)
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
