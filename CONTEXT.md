# GFIT CoWork

GFIT CoWork is a local workspace application for working with Hermes Agent. It
is a distinct product identity and must not present itself as Claude Code or as
an Anthropic product.

## Language

**Hermes runtime**:
The locally configured Nous Research Hermes Agent. Hermes owns a conversation,
its history, tool activity, approval requests, and lifecycle.
_Avoid_: Claude runtime, provider adapter

**Workspace**:
One local project folder selected for the current GFIT CoWork view. Its thread
list contains only Hermes sessions belonging to that folder.
_Avoid_: global session list

**Thread**:
A Hermes session displayed inside its Workspace. Hermes, rather than GFIT
CoWork, is the durable source of truth for a Thread's history and identity.
_Avoid_: local chat copy

**Approval request**:
A Hermes request for a user's explicit allow-or-deny decision before a
potentially dangerous action proceeds. Approval is the default interaction
policy for GFIT CoWork.
_Avoid_: default full access

**CoWork host**:
The local process that starts or attaches to the Hermes gateway and presents a
purpose-built interface to the GFIT CoWork browser UI.
_Avoid_: browser-to-gateway coupling

**Recent Workspace**:
A previously opened absolute Workspace path retained only as a navigation
preference. It is not a copy of the Workspace or its Threads.
_Avoid_: native folder picker requirement

**Local-only**:
The v1 network scope in which the CoWork host accepts connections only from
the same machine. GFIT CoWork has no remote-user or shared-machine role.
_Avoid_: remote gateway support

**Live Thread**:
A Thread currently executing through another Hermes surface. GFIT CoWork can
show its activity but does not submit another prompt until the active turn has
ended.
_Avoid_: concurrent prompt submission

**CoWork host authority**:
The host's limited role as a Hermes gateway client and UI mediator. It does not
run shell commands or read or write Workspace files; Hermes alone performs
agent work.
_Avoid_: host-side workspace automation
