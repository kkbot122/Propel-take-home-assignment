import { LightningIcon } from '@phosphor-icons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { api, errorMessage } from './api/client'
import type {
  Incident,
  NetworkSubdivision,
  SimulatedFault,
  SimulatorScenario,
  SimulatorScenarioRun,
  Ticket,
} from './api/types'
import { IncidentDetail } from './components/IncidentDetail'
import { IncidentList } from './components/IncidentList'
import { ExplainabilityPanel } from './components/ExplainabilityPanel'
import { NetworkMap } from './components/NetworkMap'
import { OperationalDiagnostics } from './components/OperationalDiagnostics'

const POLL_INTERVAL_MS = 5_000
const ACTIVE_FAULTS_STORAGE_KEY = 'propel-active-simulator-faults'
const SIMULATOR_CONTROLS_ENABLED = import.meta.env.VITE_SIMULATOR_ENABLED !== 'false'

type TicketCommand =
  | { action: 'acknowledge'; ticketId: string }
  | { action: 'assign'; ticketId: string; crew: string }
  | { action: 'resolve'; ticketId: string }

type SimulatorCommand =
  | { action: 'scenario'; scenarioId: string }
  | { action: 'repair'; faultId: string; restorationFraction: number }
  | { action: 'reset' }

type SimulatorResult =
  | SimulatedFault
  | SimulatorScenarioRun
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

function explanationSignature(incident: Incident | null, ticket: Ticket | null): string | null {
  if (!incident) return null
  const stableEvidence = { ...incident.evidence }
  delete stableEvidence.analysis_at
  return JSON.stringify({
    status: incident.status,
    classification: incident.classification,
    suspected_asset_type: incident.suspected_asset_type,
    suspected_asset_id: incident.suspected_asset_id,
    precision: incident.precision,
    affected_pole_count: incident.affected_pole_count,
    confidence_score: incident.confidence_score,
    confidence_reason: incident.confidence_reason,
    evidence: stableEvidence,
    suppression_reason: incident.suppression_reason,
    ticket: ticket
      ? {
          status: ticket.status,
          restoration_status: ticket.restoration_status,
          remaining_dark_count: ticket.remaining_dark_count,
        }
      : null,
  })
}

const SCENARIO_LABELS: Record<string, string> = {
  'surveyed-span': 'Surveyed span fault',
  'inferred-span': 'Inferred span fault',
  'dt-fault': 'Transformer fault',
  'feeder-fault': 'Feeder fault',
  'scheduled-span': 'Scheduled outage',
  'noisy-span': 'Noisy telemetry',
  'dead-sensor': 'Dead sensor',
  'simultaneous-spans': 'Three simultaneous faults',
  'partial-restoration': 'Partial restoration',
}

function scenarioLabel(scenario: SimulatorScenario): string {
  return SCENARIO_LABELS[scenario.scenario_id] ?? scenario.scenario_id.replaceAll('-', ' ')
}

export function App() {
  const queryClient = useQueryClient()
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null)
  const [selectedFeederId, setSelectedFeederId] = useState('ALL')
  const [selectedTransformerId, setSelectedTransformerId] = useState('ALL')
  const [activeFaults, setActiveFaults] = useState<SimulatedFault[]>(storedActiveFaults)
  const [selectedScenarioId, setSelectedScenarioId] = useState('surveyed-span')
  const [commandMessage, setCommandMessage] = useState<string | null>(null)

  const healthQuery = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const diagnosticsQuery = useQuery({
    queryKey: ['diagnostics', 'overview'],
    queryFn: api.diagnostics,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const telemetryDiagnosticsQuery = useQuery({
    queryKey: ['diagnostics', 'telemetry'],
    queryFn: api.recentTelemetry,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const staleDevicesQuery = useQuery({
    queryKey: ['diagnostics', 'devices', 'stale'],
    queryFn: api.unhealthyDevices,
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
    queryKey: ['network', 'subdivision', 'poles'],
    queryFn: api.subdivisionPoles,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 1,
  })
  const subdivisionQuery = useQuery({
    queryKey: ['network', 'subdivision'],
    queryFn: api.subdivision,
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  })
  const scenariosQuery = useQuery({
    queryKey: ['simulator', 'scenarios'],
    queryFn: api.simulatorScenarios,
    enabled: SIMULATOR_CONTROLS_ENABLED,
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  })
  const selectedScenario =
    scenariosQuery.data?.find((scenario) => scenario.scenario_id === selectedScenarioId) ?? null
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
  const transformerOptions = useMemo(
    () =>
      (subdivisionQuery.data?.transformers ?? []).filter(
        (transformer) =>
          selectedFeederId === 'ALL' || transformer.feeder_id === selectedFeederId,
      ),
    [selectedFeederId, subdivisionQuery.data],
  )
  const filteredSubdivision = useMemo<NetworkSubdivision | null>(() => {
    const subdivision = subdivisionQuery.data
    if (!subdivision) return null
    const transformers = subdivision.transformers.filter(
      (transformer) =>
        (selectedFeederId === 'ALL' || transformer.feeder_id === selectedFeederId) &&
        (selectedTransformerId === 'ALL' || transformer.dt_id === selectedTransformerId),
    )
    const transformerIds = new Set(transformers.map((transformer) => transformer.dt_id))
    const feederIds = new Set(transformers.map((transformer) => transformer.feeder_id))
    const feeders = subdivision.feeders.filter((feeder) => feederIds.has(feeder.feeder_id))
    const substationIds = new Set(feeders.map((feeder) => feeder.substation_id))
    return {
      ...subdivision,
      substations: subdivision.substations.filter((substation) =>
        substationIds.has(substation.substation_id),
      ),
      feeders,
      transformers,
      topologies: subdivision.topologies.filter((topology) =>
        transformerIds.has(topology.dt_id),
      ),
    }
  }, [selectedFeederId, selectedTransformerId, subdivisionQuery.data])
  const visibleTransformerIds = useMemo(
    () => new Set(filteredSubdivision?.transformers.map((item) => item.dt_id) ?? []),
    [filteredSubdivision],
  )
  const visiblePoles = useMemo(
    () => (polesQuery.data ?? []).filter((pole) => visibleTransformerIds.has(pole.dt_id)),
    [polesQuery.data, visibleTransformerIds],
  )
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
  const explanationReady =
    selectedIncident !== null && (selectedIncident.ticket_id === null || ticket !== null)
  const selectedExplanationSignature = useMemo(
    () => explanationSignature(selectedIncident, ticket),
    [selectedIncident, ticket],
  )
  const explanationQuery = useQuery({
    queryKey: [
      'incident-explanation',
      selectedIncident?.incident_id,
      selectedExplanationSignature,
    ],
    queryFn: () => api.explainIncident(selectedIncident?.incident_id as string),
    enabled: explanationReady,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    retry: false,
  })

  async function refreshOperationalData() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['incidents'] }),
      queryClient.invalidateQueries({ queryKey: ['incident'] }),
      queryClient.invalidateQueries({ queryKey: ['ticket'] }),
      queryClient.invalidateQueries({ queryKey: ['network', 'subdivision', 'poles'] }),
      queryClient.invalidateQueries({ queryKey: ['diagnostics'] }),
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
      if (command.action === 'scenario') return api.runSimulatorScenario(command.scenarioId)
      if (command.action === 'repair') {
        return api.repairFault(command.faultId, command.restorationFraction)
      }
      return api.resetSimulator()
    },
    onMutate: () => {
      setCommandMessage(null)
    },
    onSuccess: (result, command) => {
      if (command.action === 'scenario' && 'scenario_id' in result) {
        setActiveFaults((current) => {
          const resultFaultIds = new Set(result.faults.map((fault) => fault.fault_id))
          const updated = [
            ...current.filter((fault) => !resultFaultIds.has(fault.fault_id)),
            ...result.faults,
          ]
          sessionStorage.setItem(ACTIVE_FAULTS_STORAGE_KEY, JSON.stringify(updated))
          return updated
        })
        if (result.failed_device_id && result.failed_pole_id) {
          setCommandMessage(
            `Device ${result.failed_device_id} failed at powered pole ${result.failed_pole_id}. No outage ticket should appear.`,
          )
        } else if (result.scheduled_outage_id) {
          setCommandMessage(
            `Scheduled outage ${result.scheduled_outage_id} injected. It should be suppressed without a ticket.`,
          )
        } else {
          setCommandMessage(
            `${result.description} started with ${result.faults.length} physical fault${result.faults.length === 1 ? '' : 's'}.`,
          )
        }
      } else if (command.action === 'repair' && 'fault_id' in result) {
        setActiveFaults((current) => {
          const updated = result.status === 'REPAIRED'
            ? current.filter((fault) => fault.fault_id !== command.faultId)
            : current.map((fault) => fault.fault_id === command.faultId ? result : fault)
          sessionStorage.setItem(ACTIVE_FAULTS_STORAGE_KEY, JSON.stringify(updated))
          return updated
        })
        setCommandMessage(
          result.status === 'REPAIRED'
            ? 'Full repair telemetry sent. Waiting for telemetry-only verification and closure.'
            : `Partial restoration reached ${result.restored_pole_ids.length} poles. Remaining dark evidence keeps the ticket open.`,
        )
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
    subdivisionQuery.dataUpdatedAt,
    incidentQuery.dataUpdatedAt,
    ticketQuery.dataUpdatedAt,
  )
  const backendError = queryError(
    healthQuery.error,
    incidentsQuery.error,
    suppressedIncidentsQuery.error,
    polesQuery.error,
    subdivisionQuery.error,
    incidentQuery.error,
    ticketQuery.error,
  )
  const diagnosticsError = queryError(
    diagnosticsQuery.error,
    telemetryDiagnosticsQuery.error,
    staleDevicesQuery.error,
  )
  const mutationError = queryError(ticketMutation.error, simulatorMutation.error)
  const backendHealthy =
    healthQuery.data?.status === 'healthy' &&
    diagnosticsQuery.data?.status === 'healthy' &&
    backendError === null &&
    diagnosticsError === null
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

      <section className="overview-screen" aria-label="Outage operations overview">
        <div className="overview-messages">
          {(backendError || healthQuery.data?.status === 'unhealthy') && (
            <div className="failure-banner" role="alert">
              <strong>Live backend data is unavailable.</strong>
              <span>{backendError ?? 'One or more backend dependencies are unhealthy.'}</span>
              <button type="button" onClick={() => void queryClient.refetchQueries()}>
                Retry now
              </button>
            </div>
          )}

          {(commandMessage || mutationError) && (
            <div className={`command-message${mutationError ? ' error' : ''}`} role="status">
              {mutationError ?? commandMessage}
            </div>
          )}
        </div>

        <div className="overview-grid">
          <div className="overview-left">
            <section className="panel map-panel" aria-labelledby="map-title">
              <div className="panel-heading map-heading">
                <div>
                  <p className="section-label">
                    Anjanapura · Konanakunte · Kothnur · JP Nagar
                  </p>
                  <h2 id="map-title">
                    {subdivisionQuery.data?.name ?? 'South Bengaluru subdivision'}
                  </h2>
                </div>
                <div className="map-heading-tools">
                  <span className="map-focus-label">
                    {selectedIncident
                      ? `Focused · ${selectedIncident.suspected_asset_id
                          .replace('->', ' → ')
                          .replace('..', ' ⇢ ')}`
                      : 'Subdivision overview'}
                  </span>
                  <div className="map-filters" aria-label="Network map filters">
                    <label>
                      <span>Feeder</span>
                      <select
                        aria-label="Filter map by feeder"
                        value={selectedFeederId}
                        onChange={(event) => {
                          setSelectedFeederId(event.target.value)
                          setSelectedTransformerId('ALL')
                        }}
                      >
                        <option value="ALL">All feeders</option>
                        {subdivisionQuery.data?.feeders.map((feeder) => (
                          <option key={feeder.feeder_id} value={feeder.feeder_id}>
                            {feeder.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>Transformer</span>
                      <select
                        aria-label="Filter map by transformer"
                        value={selectedTransformerId}
                        onChange={(event) => setSelectedTransformerId(event.target.value)}
                      >
                        <option value="ALL">All DTs</option>
                        {transformerOptions.map((transformer) => (
                          <option key={transformer.dt_id} value={transformer.dt_id}>
                            {transformer.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </div>
              </div>
              {(polesQuery.isPending && !polesQuery.data) ||
              (subdivisionQuery.isPending && !subdivisionQuery.data) ? (
                <div className="map-loading" role="status">
                  <span className="spinner" aria-hidden="true" />
                  Loading subdivision network…
                </div>
              ) : (
                <NetworkMap
                  poles={visiblePoles}
                  subdivision={filteredSubdivision}
                  selectedIncident={selectedIncident}
                  showPoleLabels={selectedTransformerId !== 'ALL'}
                />
              )}
              <div className="map-footer">
                <span>
                  {filteredSubdivision?.substations.length ?? 0} substations ·{' '}
                  {filteredSubdivision?.feeders.length ?? 0} feeders ·{' '}
                  {filteredSubdivision?.transformers.length ?? 0} DTs
                </span>
                <span>
                  {visiblePoles.filter((pole) => pole.state === 'LIVE').length}/{visiblePoles.length}{' '}
                  poles live ·{' '}
                  {filteredSubdivision?.topologies.filter(
                    (topology) => topology.source === 'INFERRED',
                  ).length ?? 0}{' '}
                  inferred DTs
                </span>
              </div>
            </section>

            <ExplainabilityPanel
              incident={selectedIncident}
              explanation={explanationQuery.data ?? null}
              loading={explanationReady && explanationQuery.isPending}
              error={explanationQuery.error ? errorMessage(explanationQuery.error) : null}
            />
          </div>

          <div className="overview-right">
            {SIMULATOR_CONTROLS_ENABLED && (
              <section className="scenario-bar" aria-labelledby="scenario-title">
                <div className="scenario-copy">
                  <span className="scenario-icon" aria-hidden="true">
                    <LightningIcon size={19} weight="fill" />
                  </span>
                  <div>
                    <div className="scenario-heading">
                      <h2 id="scenario-title">Simulator</h2>
                      <span
                        className={`scenario-status${activeFaults.length > 0 ? ' active' : ''}`}
                      >
                        {activeFaults.length > 0
                          ? `${activeFaults.length} active fault${
                              activeFaults.length === 1 ? '' : 's'
                            }`
                          : 'Ready'}
                      </span>
                    </div>
                    <p id="scenario-description">
                      {selectedScenario?.description ?? 'Choose a field condition to simulate.'}
                    </p>
                  </div>
                </div>
                <div className="scenario-actions">
                  <label className="scenario-picker">
                    <span>Scenario</span>
                    <select
                      aria-describedby="scenario-description"
                      value={selectedScenarioId}
                      onChange={(event) => setSelectedScenarioId(event.target.value)}
                      disabled={operationPending || scenariosQuery.isPending}
                    >
                      {(scenariosQuery.data ?? []).map((scenario) => (
                        <option key={scenario.scenario_id} value={scenario.scenario_id}>
                          {scenarioLabel(scenario)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    className="inject-button"
                    onClick={() => {
                      setSelectedIncidentId(null)
                      simulatorMutation.mutate({
                        action: 'scenario',
                        scenarioId: selectedScenarioId,
                      })
                    }}
                    disabled={
                      operationPending || scenariosQuery.isPending || activeFaults.length > 0
                    }
                  >
                    Run scenario
                  </button>
                  {selectedSimulatorFault && selectedTicketId && (
                    <div
                      className="scenario-repair-actions"
                      aria-label="Selected fault repair actions"
                    >
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() =>
                          simulatorMutation.mutate({
                            action: 'repair',
                            faultId: selectedSimulatorFault.fault_id,
                            restorationFraction: 0.5,
                          })
                        }
                        disabled={operationPending}
                      >
                        Restore 50%
                      </button>
                      <button
                        type="button"
                        className="repair-button"
                        onClick={() =>
                          simulatorMutation.mutate({
                            action: 'repair',
                            faultId: selectedSimulatorFault.fault_id,
                            restorationFraction: 1,
                          })
                        }
                        disabled={operationPending}
                      >
                        Complete repair
                      </button>
                    </div>
                  )}
                  <button
                    type="button"
                    className="scenario-reset"
                    onClick={() => {
                      setSelectedIncidentId(null)
                      simulatorMutation.mutate({ action: 'reset' })
                    }}
                    disabled={operationPending}
                  >
                    Reset
                  </button>
                </div>
              </section>
            )}

            <section className="findings-workspace" aria-label="Current incident findings">
              <IncidentList
                incidents={incidents}
                selectedIncidentId={effectiveSelectedIncidentId}
                onSelect={setSelectedIncidentId}
                loading={incidentsQuery.isPending || suppressedIncidentsQuery.isPending}
              />

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
          </div>
        </div>
      </section>

      <section className="diagnostics-screen" aria-label="System evidence and diagnostics">
        <OperationalDiagnostics
          diagnostics={diagnosticsQuery.data ?? null}
          telemetry={telemetryDiagnosticsQuery.data?.items ?? []}
          staleDevices={staleDevicesQuery.data?.items ?? []}
          loading={diagnosticsQuery.isPending}
          error={diagnosticsError}
          onRetry={() => void queryClient.refetchQueries({ queryKey: ['diagnostics'] })}
        />
      </section>

      <footer className="app-footer">
        <span>Propel · PB-09 operational diagnostics</span>
        <span>Polling every 5 seconds · verification remains telemetry-only</span>
      </footer>
    </main>
  )
}
