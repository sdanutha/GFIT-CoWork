import { createCoWorkHost } from './co-work-host.js'
import { createLocalHermesWorkspaceGateway } from './local-hermes-workspace-gateway.js'

const host = createCoWorkHost(createLocalHermesWorkspaceGateway())
await host.listen(Number(process.env.PORT ?? 4318))

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, async () => {
    await host.close()
    process.exit(0)
  })
}
