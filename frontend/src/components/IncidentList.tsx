import { useMemo, useState } from 'react'

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
  const [findingFilter, setFindingFilter] = useState<'ALL' | 'ACTIONABLE' | 'SUPPRESSED'>('ALL')
  const [classificationFilter, setClassificationFilter] = useState('ALL')
  const classifications = useMemo(
    () => [...new Set(incidents.map((incident) => incident.classification))].sort(),
    [incidents],
  )
  const visibleIncidents = useMemo(
    () =>
      incidents.filter(
        (incident) =>
          (findingFilter === 'ALL' ||
            (findingFilter === 'SUPPRESSED' && incident.status === 'SUPPRESSED') ||
            (findingFilter === 'ACTIONABLE' && incident.status !== 'SUPPRESSED')) &&
          (classificationFilter === 'ALL' || incident.classification === classificationFilter),
      ),
    [classificationFilter, findingFilter, incidents],
  )
  return (
    <section className="panel incident-panel" aria-labelledby="incidents-title">
      <div className="panel-heading">
        <div>
          <p className="section-label">Operations and diagnostics</p>
          <h2 id="incidents-title">Current findings</h2>
        </div>
        <span className="count-badge" aria-label={`${visibleIncidents.length} current findings`}>
          {visibleIncidents.length}
        </span>
      </div>

      <div className="incident-filters" aria-label="Finding filters">
        <label>
          <span>Dispatch state</span>
          <select
            value={findingFilter}
            onChange={(event) =>
              setFindingFilter(event.target.value as 'ALL' | 'ACTIONABLE' | 'SUPPRESSED')
            }
          >
            <option value="ALL">All findings</option>
            <option value="ACTIONABLE">Actionable only</option>
            <option value="SUPPRESSED">Suppressed only</option>
          </select>
        </label>
        <label>
          <span>Classification</span>
          <select
            value={classificationFilter}
            onChange={(event) => setClassificationFilter(event.target.value)}
          >
            <option value="ALL">All classes</option>
            {classifications.map((classification) => (
              <option key={classification} value={classification}>
                {classification.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        </label>
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
      ) : visibleIncidents.length === 0 ? (
        <div className="empty-state compact-empty">
          <h3>No matching findings</h3>
          <p>Change the dispatch-state or classification filter.</p>
        </div>
      ) : (
        <ol className="incident-list">
          {visibleIncidents.map((incident) => {
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
                    <span
                      className={`status-pill status-${
                        incident.status === 'SUPPRESSED'
                          ? 'suppressed'
                          : incident.ticket_status?.toLowerCase()
                      }`}
                    >
                      {incident.status === 'SUPPRESSED'
                        ? 'SUPPRESSED'
                        : (incident.ticket_status?.replaceAll('_', ' ') ?? 'NO TICKET')}
                    </span>
                    <span className="confidence-mini">
                      Evidence score {incident.confidence_score}/100
                    </span>
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
