import type {
  DeviceHealthDiagnostic,
  OperationalDiagnostics as OperationalDiagnosticsData,
  TelemetryDiagnostic,
} from '../api/types'

interface OperationalDiagnosticsProps {
  diagnostics: OperationalDiagnosticsData | null
  telemetry: TelemetryDiagnostic[]
  staleDevices: DeviceHealthDiagnostic[]
  loading: boolean
  error: string | null
  onRetry: () => void
}

function compactTime(value: string | null): string {
  if (!value) return 'No observation'
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function count(values: Record<string, number>, key: string): number {
  return values[key] ?? 0
}

function queueValue(value: number | null): string {
  return value === null ? 'Unavailable' : value.toLocaleString()
}

export function OperationalDiagnostics({
  diagnostics,
  telemetry,
  staleDevices,
  loading,
  error,
  onRetry,
}: OperationalDiagnosticsProps) {
  if (loading && !diagnostics) {
    return (
      <section className="diagnostics-strip diagnostics-loading" aria-label="Operational diagnostics">
        <span className="spinner" aria-hidden="true" />
        Loading worker and telemetry diagnostics…
      </section>
    )
  }

  if (error && !diagnostics) {
    return (
      <section className="diagnostics-strip diagnostics-error" aria-label="Operational diagnostics">
        <div>
          <strong>Diagnostics unavailable</strong>
          <span>{error}</span>
        </div>
        <button type="button" onClick={onRetry}>Retry diagnostics</button>
      </section>
    )
  }

  if (!diagnostics) return null
  const degraded = diagnostics.status === 'degraded'

  return (
    <section
      className={`diagnostics-strip${degraded ? ' degraded' : ''}`}
      aria-labelledby="diagnostics-title"
    >
      <div className="diagnostics-heading">
        <div>
          <p className="section-label">System evidence</p>
          <h2 id="diagnostics-title">Operational diagnostics</h2>
        </div>
        <span className={`diagnostic-state ${degraded ? 'degraded' : 'healthy'}`}>
          {degraded ? 'DEGRADED' : 'HEALTHY'}
        </span>
      </div>

      <dl className="diagnostic-metrics">
        <div>
          <dt>Worker</dt>
          <dd>{diagnostics.worker.status.toUpperCase()}</dd>
          <small>{compactTime(diagnostics.worker.last_seen_at)}</small>
        </div>
        <div>
          <dt>Consumer lag</dt>
          <dd>{queueValue(diagnostics.queue.lag)}</dd>
          <small>{queueValue(diagnostics.queue.pending)} pending</small>
        </div>
        <div>
          <dt>Analysis due</dt>
          <dd>{queueValue(diagnostics.queue.analysis_pending)}</dd>
          <small>{queueValue(diagnostics.queue.analysis_overdue)} overdue</small>
        </div>
        <div>
          <dt>Dead letter</dt>
          <dd>{queueValue(diagnostics.queue.dead_letter_count)}</dd>
          <small>bounded poison-event stream</small>
        </div>
        <div>
          <dt>Pole evidence</dt>
          <dd>{count(diagnostics.pole_state_counts, 'LIVE').toLocaleString()} live</dd>
          <small>
            {count(diagnostics.pole_state_counts, 'STALE').toLocaleString()} stale ·{' '}
            {count(diagnostics.pole_state_counts, 'DARK').toLocaleString()} dark
          </small>
        </div>
      </dl>

      {diagnostics.warnings.length > 0 ? (
        <ul className="diagnostic-warnings" aria-label="Current operational warnings">
          {diagnostics.warnings.map((warning) => (
            <li key={warning.code} className={warning.severity}>
              <strong>{warning.code.replaceAll('_', ' ')}</strong>
              <span>{warning.message}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="diagnostic-clear">No dependency, queue, retry, or dead-letter warnings.</p>
      )}

      <details className="diagnostic-details">
        <summary>Inspect bounded telemetry and device health</summary>
        <div className="diagnostic-secondary-grid">
          <section aria-labelledby="telemetry-history-title">
            <h3 id="telemetry-history-title">Recent processed telemetry</h3>
            {telemetry.length === 0 ? (
              <p className="diagnostic-empty">No processed telemetry is available.</p>
            ) : (
              <ol className="telemetry-history">
                {telemetry.map((event) => (
                  <li key={event.event_id}>
                    <span className={`telemetry-dot ${event.energized ? 'live' : 'dark'}`} />
                    <div>
                      <strong>{event.pole_id} · {event.event_type.replaceAll('_', ' ')}</strong>
                      <span>{event.device_id} · sequence {event.sequence}</span>
                    </div>
                    <time dateTime={event.processed_at}>{compactTime(event.processed_at)}</time>
                  </li>
                ))}
              </ol>
            )}
          </section>
          <section aria-labelledby="device-health-title">
            <h3 id="device-health-title">Stale device health</h3>
            {staleDevices.length === 0 ? (
              <p className="diagnostic-empty">No stale devices in this bounded page.</p>
            ) : (
              <ol className="device-health-list">
                {staleDevices.map((device) => (
                  <li key={device.device_id}>
                    <strong>{device.pole_id ?? device.device_id}</strong>
                    <span>{device.dt_id ?? 'Unbound'} · {device.status_reason}</span>
                    <time dateTime={device.last_seen_at ?? undefined}>
                      {compactTime(device.last_seen_at)}
                    </time>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
        <p className="diagnostic-privacy-note">
          Raw payloads are intentionally omitted. Use event and correlation IDs for server-side tracing.
        </p>
      </details>
    </section>
  )
}
