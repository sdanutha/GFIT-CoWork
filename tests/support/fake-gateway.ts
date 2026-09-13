import type { HermesWorkspaceGateway } from '../../src/host/hermes-workspace-gateway.js'

// A fake gateway with harmless defaults, so each test overrides only the
// behavior it exercises. New seam methods get a default here once, rather
// than in every test that constructs a gateway.
export function createFakeGateway(
  overrides: Partial<HermesWorkspaceGateway> = {},
): HermesWorkspaceGateway {
  return {
    health: async () => ({ kind: 'ready', startedByCoWork: false }),
    openWorkspace: async (path) => ({ kind: 'opened', workspace: { path, threads: [] } }),
    openThread: async (threadId) => ({ kind: 'opened', history: { threadId, messages: [] } }),
    createThread: async () => ({ kind: 'created', threadId: 'fake-thread' }),
    submitPrompt: async () => {},
    stopThread: async () => {},
    respondApproval: async () => {},
    subscribe: () => () => {},
    close: async () => {},
    ...overrides,
  }
}
