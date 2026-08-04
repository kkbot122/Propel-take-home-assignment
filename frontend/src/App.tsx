import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { api, errorMessage } from './api/client'
import type { Incident, SimulatedFault, Ticket } from './api/types'
import { IncidentDetail } from './components/IncidentDetail'
import { IncidentList } from './components/IncidentList'
import { NetworkMap } from './components/NetworkMap'

const POLL_INTERVAL_MS = 5_000
const ACTIVE_FAULT_STORAGE_KEY = 'propel-active-simulator-fault'

type TicketCommand =
  | { action: 'acknowledge'; ticketId: string }
  | { action: 'assign'; ticketId: string; crew: string }
  | { action: 'resolve'; ticketId: string }

type SimulatorCommand =
  | { action: 'inject'; faultType: SimulatedFault['fault_type'] }
  | { action: 'repair'; faultId: string }
  | { action: 'reset' }

type SimulatorResult =
  | SimulatedFault
  | { status: 'reset'; repaired_faults: SimulatedFault[] }

function queryError(...errors: unknown[]): string | null {
  const error = errors.find((item) => item !== null && item !== undefined)
  return error ? errorMessage(error) : null
}

function lastRefreshLabel(timestamp: number): string {
  if (timestamp === 0) return 'Waiting for first response'
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(timestamp))
}

export function App() {
  const queryClient = useQueryClient()
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null)
  const [activeFaultId, setActiveFaultId] = useState<string | null>(() =>
    sessionStorage.getItem(ACTIVE_FAULT_STORAGE_KEY),
  )
  const [commandMessage, setCommandMessage] = useState<string | null>(null)

  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const incidentsQuery = useQuery({
    queryKey: ['incidents', 'active'],
    queryFn: api.incidents,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const suppressedIncidentsQuery = useQuery({
    queryKey: ['incidents', 'suppressed'],
    queryFn: api.suppressedIncidents,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const polesQuery = useQuery({
    queryKey: ['network', 'poles', 'FDR-001'],
    queryFn: api.poles,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const topologyQuery = useQuery({
    queryKey: ['network', 'topology', 'FDR-001'],
    queryFn: api.topologies,
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  })
  const networkOverviewQuery = useQuery({
    queryKey: ['network', 'overview', 'FDR-001'],
    queryFn: api.networkOverview,
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  })
  const activeIncidents = useMemo(() => incidentsQuery.data ?? [], [incidentsQuery.data])
  const incidents = useMemo(
    () => [...activeIncidents, ...(suppressedIncidentsQuery.data ?? [])],
    [activeIncidents, suppressedIncidentsQuery.data],
  )
  const effectiveSelectedIncidentId = selectedIncidentId ?? incidents[0]?.incident_id ?? null
  const incidentQuery = useQuery({
    queryKey: ['incident', effectiveSelectedIncidentId],
    queryFn: () => api.incident(effectiveSelectedIncidentId as string),
    enabled: effectiveSelectedIncidentId !== null,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })

  const selectedSummary = useMemo(
    () =>
      incidents.find((incident) => incident.incident_id === effectiveSelectedIncidentId) ?? null,
    [effectiveSelectedIncidentId, incidents],
  )
  const selectedIncident: Incident | null = incidentQuery.data ?? selectedSummary
  const selectedTicketId = selectedIncident?.ticket_id ?? null
  const ticketQuery = useQuery({
    queryKey: ['ticket', selectedTicketId],
    queryFn: () => api.ticket(selectedTicketId as string),
    enabled: selectedTicketId !== null,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const ticket: Ticket | null = ticketQuery.data ?? null

  async function refreshOperationalData() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['incidents'] }),
      queryClient.invalidateQueries({ queryKey: ['incident'] }),
      queryClient.invalidateQueries({ queryKey: ['ticket'] }),
      queryClient.invalidateQueries({ queryKey: ['network', 'poles'] }),
    ])
  }

  const ticketMutation = useMutation({
    mutationFn: (command: TicketCommand) => {
      if (command.action === 'acknowledge') return api.acknowledge(command.ticketId)
      if (command.action === 'assign') return api.assign(command.ticketId, command.crew)
      return api.resolve(command.ticketId)
    },
    onMutate: () => {
      setCommandMessage(null)
    },
    onSuccess: (updatedTicket) => {
      queryClient.setQueryData(['ticket', updatedTicket.ticket_id], updatedTicket)
      setCommandMessage(`Ticket moved to ${updatedTicket.status.replaceAll('_', ' ')}.`)
      void refreshOperationalData()
    },
  })

  const simulatorMutation = useMutation<SimulatorResult, Error, SimulatorCommand>({
    mutationFn: (command: SimulatorCommand) => {
      if (command.action === 'inject') return api.injectFault(command.faultType)
      if (command.action === 'repair') return api.repairFault(command.faultId)
      return api.resetSimulator()
    },
    onMutate: () => {
      setCommandMessage(null)
    },
    onSuccess: (result, command) => {
      if (command.action === 'inject' && 'fault_id' in result) {
        setActiveFaultId(result.fault_id)
        sessionStorage.setItem(ACTIVE_FAULT_STORAGE_KEY, result.fault_id)
        setCommandMessage(
          `${result.fault_type.replaceAll('_', ' ')} injected. Waiting for telemetry correlation and localization.`,
        )
      } else if (command.action === 'repair') {
        setActiveFaultId(null)
        sessionStorage.removeItem(ACTIVE_FAULT_STORAGE_KEY)
        setCommandMessage('Repair telemetry sent. Waiting for the 10-second verification window.')
      } else {
        setActiveFaultId(null)
        sessionStorage.removeItem(ACTIVE_FAULT_STORAGE_KEY)
        setCommandMessage('Simulator reset requested. Active faults are restoring through telemetry.')
      }
      void refreshOperationalData()
    },
  })

  const lastUpdatedAt = Math.max(
    incidentsQuery.dataUpdatedAt,
    suppressedIncidentsQuery.dataUpdatedAt,
    polesQuery.dataUpdatedAt,
    networkOverviewQuery.dataUpdatedAt,
    incidentQuery.dataUpdatedAt,
    ticketQuery.dataUpdatedAt,
  )
  const backendError = queryError(
    healthQuery.error,
    incidentsQuery.error,
    suppressedIncidentsQuery.error,
    polesQuery.error,
    topologyQuery.error,
    networkOverviewQuery.error,
    incidentQuery.error,
    ticketQuery.error,
  )
  const mutationError = queryError(ticketMutation.error, simulatorMutation.error)
  const backendHealthy = healthQuery.data?.status === 'healthy' && backendError === null
  const ticketStatus = ticket?.status ?? selectedIncident?.ticket_status
  const operationPending = ticketMutation.isPending || simulatorMutation.isPending

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            P
          </div>
          <div>
            <p>Distribution operations</p>
            <h1>Propel</h1>
          </div>
        </div>

        <div className="header-status" aria-live="polite">
          <span className={`health-indicator${backendHealthy ? ' online' : ' offline'}`}>
            <span aria-hidden="true" />
            {backendHealthy ? 'System online' : 'System degraded'}
          </span>
          <span className="refresh-time">Last refresh · {lastRefreshLabel(lastUpdatedAt)}</span>
        </div>
      </header>

      {(backendError || healthQuery.data?.status === 'unhealthy') && (
        <div className="failure-banner" role="alert">
          <strong>Live backend data is unavailable.</strong>
          <span>{backendError ?? 'One or more backend dependencies are unhealthy.'}</span>
          <button type="button" onClick={() => void queryClient.refetchQueries()}>
            Retry now
          </button>
        </div>
      )}

      <section className="scenario-bar" aria-labelledby="scenario-title">
        <div className="scenario-copy">
          <span className="scenario-index">01</span>
          <div>
            <p className="section-label">Fixed demonstration</p>
            <h2 id="scenario-title">FDR-001 · classified outage scenarios</h2>
            <p>Surveyed span, transformer-wide, and feeder-wide telemetry · PIN 560078</p>
          </div>
        </div>
        <div className="scenario-actions">
          <button
            type="button"
            className="inject-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({ action: 'inject', faultType: 'SPAN_FAULT' })
            }}
            disabled={operationPending || activeFaultId !== null || activeIncidents.length > 0}
          >
            <span aria-hidden="true">⚡</span>
            Inject span fault
          </button>
          <button
            type="button"
            className="inject-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({ action: 'inject', faultType: 'DT_FAULT' })
            }}
            disabled={operationPending || activeFaultId !== null || activeIncidents.length > 0}
          >
            Inject DT fault
          </button>
          <button
            type="button"
            className="inject-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({ action: 'inject', faultType: 'FEEDER_FAULT' })
            }}
            disabled={operationPending || activeFaultId !== null || activeIncidents.length > 0}
          >
            Inject feeder fault
          </button>
          <button
            type="button"
            className="repair-button"
            onClick={() => {
              if (activeFaultId) {
                setSelectedIncidentId(selectedIncident?.incident_id ?? null)
                simulatorMutation.mutate({ action: 'repair', faultId: activeFaultId })
              }
            }}
            disabled={operationPending || activeFaultId === null || ticketStatus !== 'RESOLVED'}
          >
            Send repair telemetry
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({ action: 'reset' })
            }}
            disabled={operationPending}
          >
            Reset simulation
          </button>
        </div>
      </section>

      {(commandMessage || mutationError) && (
        <div className={`command-message${mutationError ? ' error' : ''}`} role="status">
          {mutationError ?? commandMessage}
        </div>
      )}

      <section className="workspace" aria-label="Outage operations workspace">
        <IncidentList
          incidents={incidents}
          selectedIncidentId={effectiveSelectedIncidentId}
          onSelect={setSelectedIncidentId}
          loading={incidentsQuery.isPending || suppressedIncidentsQuery.isPending}
        />

        <section className="panel map-panel" aria-labelledby="map-title">
          <div className="panel-heading map-heading">
            <div>
              <p className="section-label">Surveyed network · two topology snapshots</p>
              <h2 id="map-title">FDR-001 network</h2>
            </div>
            <span className="map-focus-label">
              {selectedIncident ? `Focused · ${selectedIncident.suspected_asset_id.replace('->', ' → ')}` : 'Network overview'}
            </span>
          </div>
          {(polesQuery.isPending && !polesQuery.data) ||
          (networkOverviewQuery.isPending && !networkOverviewQuery.data) ? (
            <div className="map-loading" role="status">
              <span className="spinner" aria-hidden="true" />
              Loading surveyed network…
            </div>
          ) : (
            <NetworkMap
              poles={polesQuery.data ?? []}
              topologies={topologyQuery.data ?? []}
              overview={networkOverviewQuery.data ?? null}
              selectedIncident={selectedIncident}
            />
          )}
          <div className="map-footer">
            <span>
              {networkOverviewQuery.data?.transformers.length ?? 0} DTs · surveyed topology
            </span>
            <span>
              {polesQuery.data?.filter((pole) => pole.state === 'LIVE').length ?? 0}/
              {polesQuery.data?.length ?? 0} poles live
            </span>
          </div>
        </section>

        <IncidentDetail
          incident={selectedIncident}
          ticket={ticket}
          loading={
            effectiveSelectedIncidentId !== null &&
            (incidentQuery.isPending || (selectedTicketId !== null && ticketQuery.isPending))
          }
          actionPending={ticketMutation.isPending}
          onAcknowledge={() => {
            if (selectedTicketId) {
              setSelectedIncidentId(selectedIncident?.incident_id ?? null)
              ticketMutation.mutate({ action: 'acknowledge', ticketId: selectedTicketId })
            }
          }}
          onAssign={(crew) => {
            if (selectedTicketId) {
              setSelectedIncidentId(selectedIncident?.incident_id ?? null)
              ticketMutation.mutate({ action: 'assign', ticketId: selectedTicketId, crew })
            }
          }}
          onResolve={() => {
            if (selectedTicketId) {
              setSelectedIncidentId(selectedIncident?.incident_id ?? null)
              ticketMutation.mutate({ action: 'resolve', ticketId: selectedTicketId })
            }
          }}
        />
      </section>

      <footer className="app-footer">
        <span>Propel · PB-02 span, DT, and feeder classification</span>
        <span>Polling every 5 seconds · verification remains telemetry-only</span>
      </footer>
    </main>
  )
}
