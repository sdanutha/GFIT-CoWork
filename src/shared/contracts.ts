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
