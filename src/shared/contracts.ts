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
