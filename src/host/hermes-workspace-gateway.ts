import type {
  ApprovalChoice,
  CreateThreadResult,
  HermesReadiness,
  OpenThreadResult,
  OpenWorkspaceResult,
  ThreadStreamEvent,
} from '../shared/contracts.js'

export interface HermesWorkspaceGateway {
  health(): Promise<HermesReadiness>
  openWorkspace(path: string): Promise<OpenWorkspaceResult>
  openThread(threadId: string): Promise<OpenThreadResult>
  createThread(cwd: string, title?: string): Promise<CreateThreadResult>
  submitPrompt(threadId: string, text: string): Promise<void>
  stopThread(threadId: string): Promise<void>
  respondApproval(threadId: string, requestId: string, choice: ApprovalChoice): Promise<void>
  subscribe(threadId: string, listener: (event: ThreadStreamEvent) => void): () => void
  close(): Promise<void>
}
