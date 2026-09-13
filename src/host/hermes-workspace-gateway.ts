import type { HermesReadiness, OpenWorkspaceResult } from '../shared/contracts.js'

export interface HermesWorkspaceGateway {
  health(): Promise<HermesReadiness>
  openWorkspace(path: string): Promise<OpenWorkspaceResult>
  close(): Promise<void>
}
