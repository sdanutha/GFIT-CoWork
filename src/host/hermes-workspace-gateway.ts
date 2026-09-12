import type { HermesReadiness } from '../shared/contracts.js'

export interface HermesWorkspaceGateway {
  health(): Promise<HermesReadiness>
  close(): Promise<void>
}
