export type HermesReadiness =
  | { kind: 'ready'; startedByCoWork: boolean }
  | { kind: 'unavailable'; remedy: string }

export type HealthResponse =
  | { status: 'ready'; runtime: 'Hermes'; startedByCoWork: boolean }
  | { status: 'unavailable'; runtime: 'Hermes'; remedy: string }

export function healthResponse(readiness: HermesReadiness): HealthResponse {
  return readiness.kind === 'ready'
    ? { status: 'ready', runtime: 'Hermes', startedByCoWork: readiness.startedByCoWork }
    : { status: 'unavailable', runtime: 'Hermes', remedy: readiness.remedy }
}

export type ThreadActivity = 'idle' | 'live'

export type Thread = {
  id: string
  title: string
  updatedAt: string
  activity: ThreadActivity
}

export type Workspace = {
  path: string
  threads: Thread[]
}

export type WorkspaceValidationReason =
  | 'not-absolute'
  | 'not-found'
  | 'not-a-directory'
  | 'unreadable'

export type WorkspaceErrorReason = WorkspaceValidationReason | 'unavailable'

export type OpenWorkspaceResult =
  | { kind: 'opened'; workspace: Workspace }
  | { kind: 'error'; reason: WorkspaceValidationReason }

export type WorkspaceResponse =
  | { status: 'opened'; path: string; threads: Thread[] }
  | { status: 'error'; reason: WorkspaceErrorReason; message: string }

const workspaceErrorMessages: Record<WorkspaceErrorReason, string> = {
  'not-absolute': 'Enter an absolute folder path.',
  'not-found': 'That folder does not exist on this machine.',
  'not-a-directory': 'That path is not a folder.',
  unreadable: 'That folder cannot be read. Check its permissions.',
  unavailable: 'GFIT CoWork could not reach Hermes. Check Hermes, then retry.',
}

export function workspaceErrorMessage(reason: WorkspaceErrorReason): string {
  return workspaceErrorMessages[reason]
}

export function workspaceResponse(result: OpenWorkspaceResult): WorkspaceResponse {
  return result.kind === 'opened'
    ? { status: 'opened', path: result.workspace.path, threads: result.workspace.threads }
    : { status: 'error', reason: result.reason, message: workspaceErrorMessages[result.reason] }
}

export type MessageRole = 'user' | 'assistant' | 'tool' | 'system'

export type ThreadMessage = {
  id?: string
  role: MessageRole
  text: string
}

export type ThreadHistory = {
  threadId: string
  messages: ThreadMessage[]
}

export type ThreadValidationReason = 'not-found' | 'unreadable'
export type ThreadErrorReason = ThreadValidationReason | 'unavailable'

export type OpenThreadResult =
  | { kind: 'opened'; history: ThreadHistory }
  | { kind: 'error'; reason: ThreadValidationReason }

export type ThreadResponse =
  | { status: 'opened'; threadId: string; messages: ThreadMessage[] }
  | { status: 'error'; reason: ThreadErrorReason; message: string }

const threadErrorMessages: Record<ThreadErrorReason, string> = {
  'not-found': 'That Thread no longer exists in Hermes.',
  unreadable: 'That Thread could not be read. It may have been changed in Hermes.',
  unavailable: 'GFIT CoWork could not reach Hermes. Check Hermes, then retry.',
}

export function threadErrorMessage(reason: ThreadErrorReason): string {
  return threadErrorMessages[reason]
}

export function threadResponse(result: OpenThreadResult): ThreadResponse {
  return result.kind === 'opened'
    ? { status: 'opened', threadId: result.history.threadId, messages: result.history.messages }
    : { status: 'error', reason: result.reason, message: threadErrorMessages[result.reason] }
}

export type CreateThreadResult =
  | { kind: 'created'; threadId: string }
  | { kind: 'error' }

export type CreateThreadResponse =
  | { status: 'created'; threadId: string }
  | { status: 'error'; message: string }

const unreachableHermesMessage = 'GFIT CoWork could not reach Hermes. Check Hermes, then retry.'

export function createThreadResponse(result: CreateThreadResult): CreateThreadResponse {
  return result.kind === 'created'
    ? { status: 'created', threadId: result.threadId }
    : { status: 'error', message: unreachableHermesMessage }
}

export function hermesUnavailableMessage(): string {
  return unreachableHermesMessage
}

export type ApprovalChoice = 'allow' | 'deny'
export type ApprovalDecision = 'allowed' | 'denied' | 'expired' | 'failed'

// Live turn events forwarded from Hermes to the browser over SSE.
export type ThreadStreamEvent =
  | { kind: 'turn-start' }
  | { kind: 'message-start'; role: MessageRole }
  | { kind: 'message-delta'; text: string }
  | { kind: 'message-complete' }
  | { kind: 'tool-start'; tool: string }
  | { kind: 'tool-end'; tool: string; summary: string; details?: string }
  | { kind: 'approval-request'; requestId: string; action: string }
  | { kind: 'approval-resolved'; requestId: string; decision: ApprovalDecision }
  | { kind: 'turn-end' }
  | { kind: 'turn-error'; message: string }
