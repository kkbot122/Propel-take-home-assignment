import type { Incident } from '../api/types'

interface IncidentListProps {
  incidents: Incident[]
  selectedIncidentId: string | null
  onSelect: (incidentId: string) => void
  loading: boolean
}

function detectedTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

export function IncidentList({
  incidents,
  selectedIncidentId,
  onSelect,
  loading,
}: IncidentListProps) {
  return (
    <section className="panel incident-panel" aria-labelledby="incidents-title">
      <div className="panel-heading">
        <div>
          <p className="section-label">Operations queue</p>
          <h2 id="incidents-title">Active incidents</h2>
        </div>
        <span className="count-badge" aria-label={`${incidents.length} active incidents`}>
          {incidents.length}
        </span>
      </div>

      {loading && incidents.length === 0 ? (
        <div className="loading-block" role="status">
          <span className="spinner" aria-hidden="true" />
          Loading incidents…
        </div>
      ) : incidents.length === 0 ? (
        <div className="empty-state">
          <span className="empty-icon" aria-hidden="true">
            ✓
          </span>
          <h3>No active outages</h3>
          <p>The surveyed DT-001 network is clear. Inject the fixed scenario to begin.</p>
        </div>
      ) : (
        <ol className="incident-list">
          {incidents.map((incident) => {
            const selected = incident.incident_id === selectedIncidentId
            return (
              <li key={incident.incident_id}>
                <button
                  type="button"
                  className={`incident-card${selected ? ' selected' : ''}`}
                  aria-pressed={selected}
                  onClick={() => onSelect(incident.incident_id)}
                >
                  <span className="incident-card-topline">
                    <span className="fault-class">{incident.classification.replaceAll('_', ' ')}</span>
                    <time dateTime={incident.detected_at}>{detectedTime(incident.detected_at)}</time>
                  </span>
                  <strong>{incident.suspected_asset_id.replace('->', ' → ')}</strong>
                  <span className="incident-metrics">
                    <span>{incident.affected_pole_count} poles</span>
                    <span>{incident.precision.replaceAll('_', ' ')}</span>
                  </span>
                  <span className="incident-card-footer">
                    <span className={`status-pill status-${incident.ticket_status?.toLowerCase()}`}>
                      {incident.ticket_status?.replaceAll('_', ' ') ?? 'NO TICKET'}
                    </span>
                    <span className="confidence-mini">{incident.confidence_score}% evidence</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
