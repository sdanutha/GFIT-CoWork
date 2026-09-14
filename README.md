# GFIT CoWork

GFIT CoWork is a local web client for working with Hermes Agent. The current
release checks whether Hermes is ready and shows safe setup guidance when it is
not.

## Requirements

- Node.js 20.19 or newer (or Node.js 22.12 or newer)
- Hermes Agent installed locally and available as `hermes`
- A Hermes profile configured outside GFIT CoWork with `hermes setup`

GFIT CoWork does not ask for or store Hermes credentials.

## Development

1. Install the project dependencies:

   ```sh
   npm install
   ```

2. Start the local CoWork host:

   ```sh
   npm run dev
   ```

   The host attaches to a ready local Hermes gateway or starts one when needed.

3. In another terminal, start the browser client:

   ```sh
   npm run dev:client
   ```

4. Open <http://127.0.0.1:5173>.

Both development services listen only on the local machine. If the app reports
that Hermes is unavailable, check the Hermes installation and profile, then use
the **Retry** button.

## Agent continuity

This repository can move between Codex, Claude, Copilot, Hermes, and other agents without copying conversation history. Read [AGENTS.md](AGENTS.md), [CONTEXT.md](CONTEXT.md), and the [current handoff](.agents/handoffs/current.md) before resuming work. The [handoff protocol](.agents/handoffs/README.md) explains how to pause or transfer a task safely.

## Verification

Run the complete test and production-build checks:

```sh
npm test
npm run build
```
