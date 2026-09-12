import { useCallback, useEffect, useState } from 'react'
import type { HealthResponse } from '../shared/contracts.js'

const unavailableHealth: HealthResponse = {
  status: 'unavailable',
  runtime: 'Hermes',
  remedy: 'Check your Hermes setup, then retry.',
}

export async function requestHealth(fetcher: typeof fetch = fetch): Promise<HealthResponse> {
  try {
    const response = await fetcher('/api/health')
    if (!response.ok) return unavailableHealth
    return await response.json() as HealthResponse
  } catch {
    return unavailableHealth
  }
}

type AppProps = {
  health?: HealthResponse
  onRetry?: () => void
}

export function App({ health, onRetry }: AppProps) {
  const [currentHealth, setCurrentHealth] = useState<HealthResponse | null>(health ?? null)
  const loadHealth = useCallback(async () => {
    setCurrentHealth(null)
    setCurrentHealth(await requestHealth())
  }, [])

  useEffect(() => {
    if (health === undefined) void loadHealth()
  }, [health, loadHealth])

  const retry = onRetry ?? (() => { void loadHealth() })

  return (
    <main className="app-shell">
      <article className="status-card" aria-labelledby="product-name">
        <div className="brand-mark" aria-hidden="true">G</div>
        <p className="eyebrow">Local Hermes workspace</p>
        <h1 id="product-name">GFIT CoWork</h1>
        {currentHealth === null ? (
          <section className="status-panel">
            <p className="status-heading" role="status">Checking Hermes…</p>
            <p className="status-copy">Connecting to the Hermes runtime on this machine.</p>
          </section>
        ) : currentHealth.status === 'ready' ? (
          <section className="status-panel">
            <p className="status-heading" role="status">Hermes is ready</p>
            <p className="status-copy">
              GFIT CoWork is connected to your local Hermes runtime.
            </p>
          </section>
        ) : (
          <section className="status-panel status-panel--unavailable">
            <p className="status-heading" role="alert">Hermes is unavailable</p>
            <p className="status-copy">{currentHealth.remedy}</p>
            <button className="retry-button" type="button" onClick={retry}>Retry</button>
          </section>
        )}
        <p className="local-note">Runs locally on this machine.</p>
      </article>
    </main>
  )
}
