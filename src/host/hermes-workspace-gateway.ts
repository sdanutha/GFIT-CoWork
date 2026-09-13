import type {
  HermesReadiness,
  OpenThreadResult,
  OpenWorkspaceResult,
} from '../shared/contracts.js'

export interface HermesWorkspaceGateway {
  health(): Promise<HermesReadiness>
  openWorkspace(path: string): Promise<OpenWorkspaceResult>
  openThread(threadId: string): Promise<OpenThreadResult>
  close(): Promise<void>
}
