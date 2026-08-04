import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { api, errorMessage } from './api/client'
import type { Incident, InjectFaultRequest, SimulatedFault, Ticket } from './api/types'
import { IncidentDetail } from './components/IncidentDetail'
import { IncidentList } from './components/IncidentList'
import { NetworkMap } from './components/NetworkMap'

const POLL_INTERVAL_MS = 5_000
const ACTIVE_FAULTS_STORAGE_KEY = 'propel-active-simulator-faults'

type TicketCommand =
  | { action: 'acknowledge'; ticketId: string }
  | { action: 'assign'; ticketId: string; crew: string }
  | { action: 'resolve'; ticketId: string }

type SimulatorCommand =
  | { action: 'inject'; request: InjectFaultRequest }
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

function storedActiveFaults(): SimulatedFault[] {
  try {
    const stored = sessionStorage.getItem(ACTIVE_FAULTS_STORAGE_KEY)
    return stored ? (JSON.parse(stored) as SimulatedFault[]) : []
  } catch {
    return []
  }
}

function faultAssetId(fault: SimulatedFault): string | null {
  if (fault.fault_type === 'SPAN_FAULT') {
    return fault.parent_pole_id && fault.child_pole_id
      ? `${fault.parent_pole_id}->${fault.child_pole_id}`
      : null
  }
  return fault.fault_type === 'DT_FAULT' ? fault.dt_id : fault.feeder_id
}

function faultExplainsIncident(fault: SimulatedFault, incident: Incident | null): boolean {
  if (!incident) return false
  if (faultAssetId(fault) === incident.suspected_asset_id) return true
  return (
    incident.precision === 'CORRIDOR' &&
    incident.classification === 'SPAN_FAULT' &&
    incident.affected_pole_ids.length > 0 &&
    incident.affected_pole_ids.every((poleId) => fault.deenergized_pole_ids.includes(poleId))
  )
}

export function App() {
  const queryClient = useQueryClient()
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null)
  const [activeFaults, setActiveFaults] = useState<SimulatedFault[]>(storedActiveFaults)
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
  const selectedSimulatorFault =
    activeFaults.find((fault) => faultExplainsIncident(fault, selectedIncident)) ?? null
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
      if (command.action === 'inject') return api.injectFault(command.request)
      if (command.action === 'repair') return api.repairFault(command.faultId)
      return api.resetSimulator()
    },
    onMutate: () => {
      setCommandMessage(null)
    },
    onSuccess: (result, command) => {
      if (command.action === 'inject' && 'fault_id' in result) {
        setActiveFaults((current) => {
          const updated = [...current.filter((fault) => fault.fault_id !== result.fault_id), result]
          sessionStorage.setItem(ACTIVE_FAULTS_STORAGE_KEY, JSON.stringify(updated))
          return updated
        })
        setCommandMessage(
          `${result.fault_type.replaceAll('_', ' ')} injected. Waiting for telemetry correlation and localization.`,
        )
      } else if (command.action === 'repair') {
        setActiveFaults((current) => {
          const updated = current.filter((fault) => fault.fault_id !== command.faultId)
          sessionStorage.setItem(ACTIVE_FAULTS_STORAGE_KEY, JSON.stringify(updated))
          return updated
        })
        setCommandMessage('Repair telemetry sent. Waiting for the 10-second verification window.')
      } else {
        setActiveFaults([])
        sessionStorage.removeItem(ACTIVE_FAULTS_STORAGE_KEY)
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
              simulatorMutation.mutate({
                action: 'inject',
                request: {
                  fault_type: 'SPAN_FAULT',
                  dt_id: 'DT-001',
                  parent_pole_id: 'P-001',
                  child_pole_id: 'P-002',
                },
              })
            }}
            disabled={
              operationPending ||
              activeFaults.some((fault) =>
                fault.deenergized_pole_ids.some((poleId) =>
                  ['P-002', 'P-003', 'P-004'].includes(poleId),
                ),
              )
            }
          >
            <span aria-hidden="true">⚡</span>
            Inject span fault A
          </button>
          <button
            type="button"
            className="inject-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({
                action: 'inject',
                request: {
                  fault_type: 'SPAN_FAULT',
                  dt_id: 'DT-002',
                  parent_pole_id: 'P-101',
                  child_pole_id: 'P-102',
                },
              })
            }}
            disabled={
              operationPending ||
              activeFaults.some((fault) => fault.deenergized_pole_ids.includes('P-102'))
            }
          >
            Inject span fault B
          </button>
          <button
            type="button"
            className="inject-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({
                action: 'inject',
                request: {
                  fault_type: 'SPAN_FAULT',
                  dt_id: 'DT-001',
                  parent_pole_id: 'P-001',
                  child_pole_id: 'P-002',
                  missing_device_pole_ids: ['P-002'],
                },
              })
            }}
            disabled={
              operationPending ||
              activeFaults.some((fault) =>
                fault.deenergized_pole_ids.some((poleId) =>
                  ['P-002', 'P-003', 'P-004'].includes(poleId),
                ),
              )
            }
          >
            Inject corridor fault
          </button>
          <button
            type="button"
            className="inject-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({
                action: 'inject',
                request: { fault_type: 'DT_FAULT', dt_id: 'DT-001' },
              })
            }}
            disabled={
              operationPending ||
              activeFaults.some((fault) =>
                fault.deenergized_pole_ids.some((poleId) =>
                  ['P-001', 'P-002', 'P-003', 'P-004'].includes(poleId),
                ),
              )
            }
          >
            Inject DT fault
          </button>
          <button
            type="button"
            className="inject-button"
            onClick={() => {
              setSelectedIncidentId(null)
              simulatorMutation.mutate({
                action: 'inject',
                request: { fault_type: 'FEEDER_FAULT', feeder_id: 'FDR-001' },
              })
            }}
            disabled={operationPending || activeFaults.length > 0}
          >
            Inject feeder fault
          </button>
          <button
            type="button"
            className="repair-button"
            onClick={() => {
              if (selectedSimulatorFault) {
                setSelectedIncidentId(selectedIncident?.incident_id ?? null)
                simulatorMutation.mutate({
                  action: 'repair',
                  faultId: selectedSimulatorFault.fault_id,
                })
              }
            }}
            disabled={
              operationPending || selectedSimulatorFault === null || ticketStatus !== 'RESOLVED'
            }
          >
            Send selected repair telemetry
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
              {selectedIncident
                ? `Focused · ${selectedIncident.suspected_asset_id
                    .replace('->', ' → ')
                    .replace('..', ' ⇢ ')}`
                : 'Network overview'}
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
        <span>Propel · PB-04 honest corridor precision</span>
        <span>Polling every 5 seconds · verification remains telemetry-only</span>
      </footer>
    </main>
  )
}
