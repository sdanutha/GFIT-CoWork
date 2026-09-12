import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { healthResponse } from '../shared/contracts.js'
import type { HermesWorkspaceGateway } from './hermes-workspace-gateway.js'

export function createCoWorkHost(gateway: HermesWorkspaceGateway) {
  const server = createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/api/health') {
      const body = JSON.stringify(healthResponse(await gateway.health()))
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
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  }
}
