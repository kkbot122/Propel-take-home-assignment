import { type FormEvent, useState } from 'react'

import type { Incident, Ticket, TicketStatus } from '../api/types'

interface IncidentDetailProps {
  incident: Incident | null
  ticket: Ticket | null
  loading: boolean
  actionPending: boolean
  onAcknowledge: () => void
  onAssign: (crew: string) => void
  onResolve: () => void
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function evidenceReasons(incident: Incident | null): {
  positive: string[]
  negative: string[]
} {
  if (!incident) return { positive: [], negative: [] }
  const candidate = incident.evidence.candidate
  if (!isRecord(candidate)) return { positive: [], negative: [] }
  return {
    positive: stringList(candidate.positive_reasons),
    negative: stringList(candidate.negative_reasons),
  }
}

function corridorEvidence(incident: Incident | null): {
  orderedPoleIds: string[]
  skippedPoleIds: string[]
} | null {
  if (!incident) return null
  const candidate = incident.evidence.candidate
  if (!isRecord(candidate) || !isRecord(candidate.corridor)) return null
  const orderedPoleIds = stringList(candidate.corridor.ordered_pole_ids)
  const skippedPoleIds = stringList(candidate.corridor.skipped_pole_ids)
  return orderedPoleIds.length >= 3 && skippedPoleIds.length > 0
    ? { orderedPoleIds, skippedPoleIds }
    : null
}

function statusLabel(status: TicketStatus): string {
  return status.replaceAll('_', ' ')
}

function shortDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

export function IncidentDetail({
  incident,
  ticket,
  loading,
  actionPending,
  onAcknowledge,
  onAssign,
  onResolve,
}: IncidentDetailProps) {
  const [crew, setCrew] = useState('Crew-7')
  const reasons = evidenceReasons(incident)
  const corridor = corridorEvidence(incident)

  function assignCrew(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedCrew = crew.trim()
    if (trimmedCrew) onAssign(trimmedCrew)
  }

  if (loading && !incident) {
    return (
      <section className="panel detail-panel" aria-label="Incident detail">
        <div className="loading-block" role="status">
          <span className="spinner" aria-hidden="true" />
          Loading incident detail…
        </div>
      </section>
    )
  }

  if (!incident) {
    return (
      <section className="panel detail-panel" aria-label="Incident detail">
        <div className="empty-state detail-empty">
          <span className="empty-icon compass" aria-hidden="true">
            +
          </span>
          <h3>No incident selected</h3>
          <p>When telemetry localizes a fault, its evidence and repair workflow appear here.</p>
        </div>
      </section>
    )
  }

  const status = ticket?.status ?? incident.ticket_status
  const suppressed = incident.status === 'SUPPRESSED'
  const restorationPending =
    status === 'RESOLVED' && ticket?.restoration_status === 'REPAIR_NOT_VERIFIED'

  return (
    <section className="panel detail-panel" aria-labelledby="detail-title">
      <div className="detail-header">
        <div>
          <p className="section-label">
            {suppressed ? 'Suppressed diagnostic' : 'Probable root fault'}
          </p>
          <h2 id="detail-title">
            {incident.suspected_asset_id.replace('->', ' → ').replace('..', ' ⇢ ')}
          </h2>
        </div>
        <span className={`status-pill status-${suppressed ? 'suppressed' : status?.toLowerCase()}`}>
          {suppressed ? 'SUPPRESSED' : status ? statusLabel(status) : 'NO TICKET'}
        </span>
      </div>

      <p className="confidence-reason">{incident.confidence_reason}</p>

      <dl className="detail-metrics">
        <div>
          <dt>Classification</dt>
          <dd>{incident.classification.replaceAll('_', ' ')}</dd>
        </div>
        <div>
          <dt>Precision</dt>
          <dd>{incident.precision.replaceAll('_', ' ')}</dd>
        </div>
        <div>
          <dt>Affected</dt>
          <dd>{incident.affected_pole_count} poles</dd>
        </div>
        <div>
          <dt>Evidence score</dt>
          <dd>{incident.confidence_score}/100</dd>
        </div>
      </dl>

      <div className="confidence-track" aria-label={`Evidence score ${incident.confidence_score} out of 100`}>
        <span style={{ width: `${incident.confidence_score}%` }} />
      </div>

      {corridor && (
        <div className="precision-notice" role="status">
          <strong>Exact span intentionally withheld</strong>
          <span>Bounded corridor: {corridor.orderedPoleIds.join(' → ')}</span>
          <small>Unusable observations: {corridor.skippedPoleIds.join(', ')}</small>
        </div>
      )}

      {incident.precision === 'DT_LEVEL' && incident.classification === 'UNCONFIRMED_OUTAGE' && (
        <div className="precision-notice dt-level" role="status">
          <strong>Transformer-level location only</strong>
          <span>No trustworthy live-to-dark corridor can be bounded from current evidence.</span>
        </div>
      )}

      <section className="evidence-section" aria-labelledby="evidence-title">
        <div className="subheading-row">
          <h3 id="evidence-title">
            {suppressed
              ? 'Why Propel suppressed dispatch'
              : corridor
                ? 'Why Propel chose this corridor'
                : `Why Propel chose this ${incident.suspected_asset_type
                  .replaceAll('_', ' ')
                  .toLowerCase()}`}
          </h3>
          <span>{incident.affected_pole_ids.join(', ')}</span>
        </div>
        <ul className="evidence-list positive-evidence">
          {reasons.positive.map((reason) => (
            <li key={reason}>
              <span aria-hidden="true">+</span>
              {reason}
            </li>
          ))}
        </ul>
        {reasons.negative.length > 0 ? (
          <ul className="evidence-list negative-evidence">
            {reasons.negative.map((reason) => (
              <li key={reason}>
                <span aria-hidden="true">−</span>
                {reason}
              </li>
            ))}
          </ul>
        ) : (
          <p className="no-contradictions">No contradictory post-onset evidence.</p>
        )}
      </section>

      {suppressed && (
        <div className="suppression-notice" role="status">
          <strong>No dispatch ticket created</strong>
          <span>{incident.suppression_reason}</span>
          <small>
            Source: {incident.suppression_source ?? 'unknown'}
            {incident.suppression_external_id
              ? ` · Reference: ${incident.suppression_external_id}`
              : ''}
          </small>
        </div>
      )}

      {restorationPending && (
        <div className="restoration-notice" role="status">
          <strong>Repair not verified</strong>
          <span>
            {ticket?.remaining_dark_count ?? 0} eligible poles remain dark. Fresh telemetry must
            stabilize before closure.
          </span>
        </div>
      )}

      {status === 'CLOSED' && (
        <div className="restoration-notice verified" role="status">
          <strong>Restoration verified</strong>
          <span>Fresh pole telemetry automatically verified and closed this ticket.</span>
        </div>
      )}

      {ticket && (
        <section className="workflow-section" aria-labelledby="workflow-title">
          <h3 id="workflow-title">Ticket workflow</h3>
          {status === 'DETECTED' && (
            <button
              type="button"
              className="primary-action"
              onClick={onAcknowledge}
              disabled={actionPending}
            >
              Acknowledge incident
            </button>
          )}
          {status === 'ACKNOWLEDGED' && (
            <form className="crew-form" onSubmit={assignCrew}>
              <label htmlFor="crew-name">Crew identifier</label>
              <div>
                <input
                  id="crew-name"
                  value={crew}
                  maxLength={120}
                  onChange={(event) => setCrew(event.target.value)}
                />
                <button type="submit" className="primary-action" disabled={actionPending || !crew.trim()}>
                  Assign crew
                </button>
              </div>
            </form>
          )}
          {status === 'CREW_ASSIGNED' && (
            <button
              type="button"
              className="primary-action warning-action"
              onClick={onResolve}
              disabled={actionPending}
            >
              Claim physical repair
            </button>
          )}
          {(status === 'RESOLVED' || status === 'VERIFIED' || status === 'CLOSED') && (
            <p className="automatic-note">
              Verification and closure are automatic; operators cannot trigger them.
            </p>
          )}

          <ol className="ticket-timeline">
            {ticket.events.map((event, index) => (
              <li key={`${event.to_status}-${event.occurred_at}-${index}`}>
                <span className="timeline-node" aria-hidden="true" />
                <div>
                  <strong>{statusLabel(event.to_status)}</strong>
                  <span>{event.reason ?? 'Status updated'}</span>
                  <time dateTime={event.occurred_at}>
                    {shortDate(event.occurred_at)} · {event.actor}
                  </time>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
    </section>
  )
}
