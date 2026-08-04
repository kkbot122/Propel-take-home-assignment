import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import type {
  HealthResponse,
  Incident,
  NetworkPole,
  NetworkSubdivision,
  NetworkTopology,
  OperationalDiagnostics,
  SimulatedFault,
  SimulatorScenario,
  Ticket,
} from './api/types'

vi.mock('./components/NetworkMap', () => ({
  NetworkMap: ({
    poles,
    subdivision,
    selectedIncident,
  }: {
    poles: NetworkPole[]
    subdivision: NetworkSubdivision | null
    selectedIncident: Incident | null
  }) => (
    <div data-testid="network-map">
      Map focus: {selectedIncident?.suspected_asset_id ?? 'network'}
      <span>
        Assets: {subdivision?.feeders.length ?? 0} feeders ·{' '}
        {subdivision?.transformers.length ?? 0} DTs · {poles.length} poles
      </span>
    </div>
  ),
}))

const health: HealthResponse = {
  status: 'healthy',
  service: 'propel-backend',
  dependencies: { database: { status: 'ok' }, redis: { status: 'ok' } },
}

const diagnostics: OperationalDiagnostics = {
  status: 'healthy',
  generated_at: '2026-08-04T01:00:00Z',
  dependencies: {
    database: { status: 'ok' },
    redis: { status: 'ok' },
  },
  worker: { status: 'ok', last_seen_at: '2026-08-04T01:00:00Z' },
  queue: {
    stream_length: 12,
    pending: 0,
    lag: 0,
    dead_letter_count: 0,
    analysis_pending: 0,
    analysis_overdue: 0,
  },
  device_counts: { HEALTHY: 1 },
  pole_state_counts: { LIVE: 1, STALE: 0, DARK: 0 },
  incident_counts: { ACTIVE: 1 },
  latest_processed_at: '2026-08-04T01:00:00Z',
  warnings: [],
}

const incident: Incident = {
  incident_id: 'incident-1',
  fingerprint: 'span:DT-001:P-001->P-002',
  status: 'ACTIVE',
  classification: 'SPAN_FAULT',
  suspected_asset_type: 'SPAN',
  suspected_asset_id: 'P-001->P-002',
  latitude: 12.88934,
  longitude: 77.58419,
  pin_code: '560078',
  affected_pole_count: 3,
  affected_pole_ids: ['P-002', 'P-003', 'P-004'],
  precision: 'EXACT_SPAN',
  confidence_score: 100,
  confidence_reason: 'Surveyed live-to-dark boundary with downstream corroboration.',
  evidence: {
    candidate: {
      positive_reasons: [
        'surveyed topology supports exact-span precision',
        'explicit DARK evidence at boundary child P-002',
      ],
      negative_reasons: [],
      score_policy_version: 'evidence-score-v1',
      raw_score: 100,
      components: {
        topology_provenance: 25,
        boundary_evidence: 30,
        downstream_corroboration: 25,
        temporal_coherence: 10,
        sensor_quality: 10,
      },
      penalties: {
        post_onset_live_contradictions: 0,
        missing_or_unhealthy_evidence: 0,
      },
      caps: [],
    },
  },
  suppression_reason: null,
  suppression_source: null,
  suppression_external_id: null,
  detected_at: '2026-08-04T01:00:00Z',
  updated_at: '2026-08-04T01:00:00Z',
  resolved_at: null,
  ticket_id: 'ticket-1',
  ticket_status: 'DETECTED',
  assigned_crew: null,
}

const suppressedIncident: Incident = {
  ...incident,
  incident_id: 'incident-suppressed',
  fingerprint: 'sensor:DT-001:DEV-P-002',
  status: 'SUPPRESSED',
  classification: 'SENSOR_ANOMALY',
  suspected_asset_type: 'DEVICE',
  suspected_asset_id: 'DEV-P-002',
  affected_pole_count: 1,
  affected_pole_ids: ['P-002'],
  precision: 'POLE_LEVEL',
  confidence_reason: 'Downstream poles remain live after the isolated dark report.',
  suppression_reason: 'The dark report contradicts fresh downstream live telemetry.',
  suppression_source: 'telemetry-consistency-rule',
  suppression_external_id: null,
  ticket_id: null,
  ticket_status: null,
}

const secondIncident: Incident = {
  ...incident,
  incident_id: 'incident-2',
  fingerprint: 'span:DT-002:P-101->P-102',
  suspected_asset_id: 'P-101->P-102',
  latitude: 12.89031,
  longitude: 77.58522,
  affected_pole_count: 1,
  affected_pole_ids: ['P-102'],
  ticket_id: 'ticket-2',
}

const corridorIncident: Incident = {
  ...incident,
  incident_id: 'incident-corridor',
  fingerprint: 'corridor:DT-001:P-001..P-003',
  suspected_asset_id: 'P-001..P-003',
  affected_pole_count: 2,
  affected_pole_ids: ['P-003', 'P-004'],
  precision: 'CORRIDOR',
  confidence_score: 74,
  confidence_reason: 'Exact boundary is hidden by one unusable pole observation.',
  evidence: {
    candidate: {
      positive_reasons: ['credible LIVE upper bound at P-001'],
      negative_reasons: ['unusable state evidence prevents an exact surveyed-span claim: P-002'],
      corridor: {
        upstream_pole_id: 'P-001',
        downstream_pole_id: 'P-003',
        ordered_pole_ids: ['P-001', 'P-002', 'P-003'],
        skipped_pole_ids: ['P-002'],
      },
    },
  },
}

const inferredIncident: Incident = {
  ...incident,
  incident_id: 'incident-inferred',
  fingerprint: 'probable-span:DT-003:P-201->P-202',
  suspected_asset_id: 'P-201->P-202',
  affected_pole_ids: ['P-202', 'P-203', 'P-204'],
  precision: 'PROBABLE_SPAN',
  confidence_score: 76,
  evidence: {
    topology_source: 'INFERRED',
    candidate: {
      positive_reasons: ['inferred topology quality is 0.84'],
      negative_reasons: [
        'geographic topology is inferred and cannot support exact-span precision',
      ],
      topology_quality_score: 0.84,
      topology_quality_tier: 'STRONGLY_INFERRED',
      topology_quality_reasons: [],
    },
  },
}

const ticket: Ticket = {
  ticket_id: 'ticket-1',
  incident_id: 'incident-1',
  status: 'DETECTED',
  assigned_crew: null,
  created_at: '2026-08-04T01:00:00Z',
  updated_at: '2026-08-04T01:00:00Z',
  resolution_claimed_at: null,
  verified_at: null,
  closed_at: null,
  restoration_status: null,
  remaining_dark_count: null,
  events: [
    {
      from_status: null,
      to_status: 'DETECTED',
      actor: 'propel-analysis',
      reason: 'actionable fault candidate detected',
      occurred_at: '2026-08-04T01:00:00Z',
      details: {},
    },
  ],
}

const simulatorScenarios: SimulatorScenario[] = [
  {
    scenario_id: 'surveyed-span',
    description: 'Surveyed live-to-dark boundary with complete telemetry',
    fault_count: 1,
    scheduled: false,
    restoration_fraction: 1,
    noise_modes: [],
  },
  {
    scenario_id: 'inferred-span',
    description: 'Hidden physical span on a topology-missing transformer',
    fault_count: 1,
    scheduled: false,
    restoration_fraction: 1,
    noise_modes: [],
  },
  {
    scenario_id: 'dt-fault',
    description: 'Transformer-wide loss with partial sensor coverage',
    fault_count: 1,
    scheduled: false,
    restoration_fraction: 1,
    noise_modes: [],
  },
  {
    scenario_id: 'feeder-fault',
    description: 'Correlated DT-wide loss across one feeder',
    fault_count: 1,
    scheduled: false,
    restoration_fraction: 1,
    noise_modes: [],
  },
  {
    scenario_id: 'scheduled-span',
    description: 'Span loss overlapping a planned-work window',
    fault_count: 1,
    scheduled: true,
    restoration_fraction: 1,
    noise_modes: [],
  },
  {
    scenario_id: 'noisy-span',
    description: 'Loss messages include omission, duplication, delay, and reordering',
    fault_count: 1,
    scheduled: false,
    restoration_fraction: 1,
    noise_modes: ['omission', 'duplication', 'delay', 'reordering'],
  },
  {
    scenario_id: 'dead-sensor',
    description: 'One healthy powered pole device stops reporting without a grid fault',
    fault_count: 0,
    scheduled: false,
    restoration_fraction: 1,
    noise_modes: [],
  },
  {
    scenario_id: 'simultaneous-spans',
    description: 'Three independent physical span faults',
    fault_count: 3,
    scheduled: false,
    restoration_fraction: 1,
    noise_modes: [],
  },
  {
    scenario_id: 'partial-restoration',
    description: 'Only half of delivered loss observations restore',
    fault_count: 1,
    scheduled: false,
    restoration_fraction: 0.5,
    noise_modes: [],
  },
]

function simulatedSpanFault(index = 1): SimulatedFault {
  return {
    fault_id: `fault-${index}`,
    fault_type: 'SPAN_FAULT',
    feeder_id: 'FDR-001',
    dt_id: `DT-00${index}`,
    parent_pole_id: index === 1 ? 'P-001' : `P-${index}01`,
    child_pole_id: index === 1 ? 'P-002' : `P-${index}02`,
    status: 'ACTIVE',
    deenergized_pole_ids: [index === 1 ? 'P-002' : `P-${index}02`],
    injected_at: '2026-08-04T01:00:00Z',
    injection_telemetry_at: '2026-08-04T01:00:00Z',
    repaired_at: null,
    emitted_event_ids: [`event-${index}`],
    restored_pole_ids: [],
    restoration_fraction: null,
  }
}

const poles: NetworkPole[] = [
  {
    pole_id: 'P-001',
    dt_id: 'DT-001',
    latitude: 12.88925,
    longitude: 77.58412,
    pin_code: '560078',
    state: 'LIVE',
    state_received_at: '2026-08-04T01:00:00Z',
    device_id: 'DEV-P-001',
  },
]

const topology: NetworkTopology = {
  dt_id: 'DT-001',
  topology_version: 1,
  source: 'SURVEYED',
  quality_score: 1,
  quality_tier: 'SURVEYED',
  quality_reasons: [],
  inference_version: null,
  spans: [
    {
      parent_pole_id: null,
      child_pole_id: 'P-001',
      source: 'SURVEYED',
      edge_confidence: 1,
      distance_m: 20,
      inference_version: null,
    },
  ],
}

const networkSubdivision: NetworkSubdivision = {
  dataset_id: 'GN-SEED-v1',
  generator_version: 'v1',
  name: 'South Bengaluru subdivision',
  neighborhoods: ['Anjanapura', 'Konanakunte', 'Kothnur', 'JP Nagar'],
  bounds: { south: 12.826, west: 77.552, north: 12.917, east: 77.62 },
  substations: [
    {
      substation_id: 'SUB-001',
      name: 'Demo Substation',
      latitude: 12.889,
      longitude: 77.5839,
      pin_code: '560078',
    },
  ],
  feeders: [{ feeder_id: 'FDR-001', name: 'Demo Feeder', substation_id: 'SUB-001' }],
  transformers: [
    {
      dt_id: 'DT-001',
      feeder_id: 'FDR-001',
      name: 'Demo DT 1',
      latitude: 12.8891,
      longitude: 77.584,
      pin_code: '560078',
    },
    {
      dt_id: 'DT-002',
      feeder_id: 'FDR-001',
      name: 'Demo DT 2',
      latitude: 12.89005,
      longitude: 77.585,
      pin_code: '560078',
    },
    {
      dt_id: 'DT-003',
      feeder_id: 'FDR-001',
      name: 'Demo DT 3',
      latitude: 12.891,
      longitude: 77.586,
      pin_code: '560078',
    },
  ],
  topologies: [
    topology,
    { ...topology, dt_id: 'DT-002', spans: [] },
    {
      ...topology,
      dt_id: 'DT-003',
      source: 'INFERRED',
      quality_score: 0.84,
      quality_tier: 'STRONGLY_INFERRED',
      inference_version: 'geo-mst-v1',
      spans: [],
    },
  ],
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function installFetchRouter(options?: {
  incidents?: Incident[]
  suppressedIncidents?: Incident[]
  unhealthy?: boolean
  diagnostics?: OperationalDiagnostics
  ticket?: Ticket
}) {
  const request = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/health') {
      return jsonResponse(options?.unhealthy ? { ...health, status: 'unhealthy' } : health)
    }
    if (url === '/api/diagnostics/overview') return jsonResponse(options?.diagnostics ?? diagnostics)
    if (url === '/api/diagnostics/telemetry?limit=10') {
      return jsonResponse({ items: [], next_cursor: null })
    }
    if (url === '/api/diagnostics/devices?status=STALE&limit=10') {
      return jsonResponse({ items: [], next_cursor: null })
    }
    if (url === '/api/incidents?status=ACTIVE&limit=100') {
      return jsonResponse(options?.incidents ?? [incident])
    }
    if (url === '/api/incidents?status=SUPPRESSED&limit=100') {
      return jsonResponse(options?.suppressedIncidents ?? [])
    }
    if (url === '/api/incidents/incident-1') return jsonResponse(incident)
    if (url === '/api/incidents/incident-2') return jsonResponse(secondIncident)
    if (url === '/api/incidents/incident-corridor') return jsonResponse(corridorIncident)
    if (url === '/api/incidents/incident-inferred') return jsonResponse(inferredIncident)
    if (url === '/api/incidents/incident-suppressed') return jsonResponse(suppressedIncident)
    if (url === '/api/tickets/ticket-1' && init?.method !== 'POST') {
      return jsonResponse(options?.ticket ?? ticket)
    }
    if (url === '/api/tickets/ticket-2' && init?.method !== 'POST') {
      return jsonResponse({ ...ticket, ticket_id: 'ticket-2', incident_id: 'incident-2' })
    }
    if (url === '/api/tickets/ticket-1/acknowledge') {
      return jsonResponse({
        ...ticket,
        status: 'ACKNOWLEDGED',
        events: [
          ...ticket.events,
          {
            from_status: 'DETECTED',
            to_status: 'ACKNOWLEDGED',
            actor: 'operator-console',
            reason: 'Alarm reviewed',
            occurred_at: '2026-08-04T01:01:00Z',
            details: {},
          },
        ],
      })
    }
    if (url === '/api/network/subdivision/poles') return jsonResponse(poles)
    if (url === '/api/network/subdivision') return jsonResponse(networkSubdivision)
    if (url === '/api/simulator/scenarios') return jsonResponse(simulatorScenarios)
    if (url.startsWith('/api/simulator/scenarios/') && url.endsWith('/run')) {
      const scenarioId = url.split('/')[4]
      const scenario = simulatorScenarios.find((item) => item.scenario_id === scenarioId)
      const faults = scenarioId === 'simultaneous-spans'
        ? [simulatedSpanFault(1), simulatedSpanFault(2), simulatedSpanFault(3)]
        : scenarioId === 'dead-sensor'
          ? []
          : [simulatedSpanFault()]
      return jsonResponse({
        scenario_id: scenarioId,
        description: scenario?.description ?? scenarioId,
        faults,
        restoration_fraction: scenario?.restoration_fraction ?? 1,
        failed_device_id: scenarioId === 'dead-sensor' ? 'DEV-P-001' : null,
        failed_pole_id: scenarioId === 'dead-sensor' ? 'P-001' : null,
        scheduled_outage_id: scenarioId === 'scheduled-span' ? 'planned-1' : null,
      })
    }
    if (url === '/api/simulator/faults/fault-1/repair') {
      return jsonResponse({
        fault_id: 'fault-1',
        fault_type: 'SPAN_FAULT',
        feeder_id: 'FDR-001',
        dt_id: 'DT-001',
        parent_pole_id: 'P-001',
        child_pole_id: 'P-002',
        status: 'REPAIRED',
        deenergized_pole_ids: ['P-002', 'P-003', 'P-004'],
        injected_at: '2026-08-04T01:00:00Z',
        injection_telemetry_at: '2026-08-04T01:00:00Z',
        repaired_at: '2026-08-04T01:03:00Z',
        emitted_event_ids: ['event-repair'],
      })
    }
    if (url === '/api/simulator/faults') {
      const payload = JSON.parse(String(init?.body)) as {
        fault_type: string
        dt_id?: string
        parent_pole_id?: string
        child_pole_id?: string
      }
      const secondSpan = payload.dt_id === 'DT-002'
      return jsonResponse(
        {
          fault_id: secondSpan ? 'fault-2' : 'fault-1',
          fault_type: payload.fault_type,
          feeder_id: 'FDR-001',
          dt_id: payload.fault_type === 'FEEDER_FAULT' ? null : (payload.dt_id ?? 'DT-001'),
          parent_pole_id: payload.fault_type === 'SPAN_FAULT' ? payload.parent_pole_id : null,
          child_pole_id: payload.fault_type === 'SPAN_FAULT' ? payload.child_pole_id : null,
          status: 'ACTIVE',
          deenergized_pole_ids: secondSpan ? ['P-102'] : ['P-002', 'P-003', 'P-004'],
          injected_at: '2026-08-04T01:00:00Z',
          injection_telemetry_at: '2026-08-04T01:00:00Z',
          repaired_at: null,
          emitted_event_ids: ['event-1'],
        },
        201,
      )
    }
    throw new Error(`Unhandled request: ${url}`)
  })
  vi.stubGlobal('fetch', request)
  return request
}

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  )
  return { queryClient, ...view }
}

describe('App', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the active incident, evidence, synchronized map, and only the valid action', async () => {
    installFetchRouter()
    renderApp()

    expect(await screen.findByRole('heading', { name: 'P-001 → P-002' })).toBeInTheDocument()
    expect(screen.getAllByText('SPAN FAULT')).toHaveLength(3)
    expect(screen.getAllByText('EXACT SPAN')).toHaveLength(2)
    expect(screen.getAllByText('3 poles')).toHaveLength(2)
    expect(screen.getByText('Evidence score 100/100')).toBeInTheDocument()
    expect(screen.queryByText('100% evidence')).not.toBeInTheDocument()
    expect(
      screen.getByText('surveyed topology supports exact-span precision'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('network-map')).toHaveTextContent('P-001->P-002')
    expect(screen.getByTestId('network-map')).toHaveTextContent('1 feeders · 3 DTs · 1 poles')
    expect(screen.getByRole('heading', { name: 'South Bengaluru subdivision' })).toBeInTheDocument()
    expect(screen.getByLabelText('Filter map by feeder')).toHaveValue('ALL')
    expect(screen.getByLabelText('Filter map by transformer')).toHaveValue('ALL')
    expect(await screen.findByRole('button', { name: 'Acknowledge incident' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Assign crew' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Claim physical repair' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete repair' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Operational diagnostics' })).toBeInTheDocument()
    expect(screen.getByText('No dependency, queue, retry, or dead-letter warnings.')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Inspect score components and caps'))
    expect(screen.getByText('topology provenance')).toBeInTheDocument()
  })

  it('applies a valid ticket action and refreshes server state', async () => {
    const fetchMock = installFetchRouter()
    renderApp()

    fireEvent.click(await screen.findByRole('button', { name: 'Acknowledge incident' }))

    expect(await screen.findByText('Ticket moved to ACKNOWLEDGED.')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tickets/ticket-1/acknowledge',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('retains the worked ticket detail after it leaves the active queue', async () => {
    const routeOptions: { incidents: Incident[] } = { incidents: [incident] }
    installFetchRouter(routeOptions)
    const { queryClient } = renderApp()

    fireEvent.click(await screen.findByRole('button', { name: 'Acknowledge incident' }))
    routeOptions.incidents = []
    await queryClient.invalidateQueries({ queryKey: ['incidents'] })

    expect(await screen.findByRole('heading', { name: 'No active outages' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'P-001 → P-002' })).toBeInTheDocument()
  })

  it('shows a healthy empty state with a compact scenario catalog', async () => {
    installFetchRouter({ incidents: [] })
    renderApp()

    expect(await screen.findByRole('heading', { name: 'No active outages' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'No incident selected' })).toBeInTheDocument()
    const scenarioPicker = await screen.findByRole('combobox', { name: 'Scenario' })
    await screen.findByRole('option', { name: 'Three simultaneous faults' })
    expect(scenarioPicker).toHaveValue('surveyed-span')
    expect(scenarioPicker.querySelectorAll('option')).toHaveLength(9)
    expect(screen.getByRole('option', { name: 'Three simultaneous faults' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run scenario' })).toBeEnabled()
    expect(screen.queryByText('Backbone quick actions')).not.toBeInTheDocument()
    expect(screen.getByText(/Last refresh/)).toBeInTheDocument()
  })

  it('keeps incidents usable while worker diagnostics are degraded', async () => {
    installFetchRouter({
      diagnostics: {
        ...diagnostics,
        status: 'degraded',
        worker: { status: 'stale', last_seen_at: null },
        warnings: [
          {
            code: 'WORKER_STALE',
            severity: 'critical',
            message: 'The telemetry worker heartbeat is missing or stale.',
          },
        ],
      },
    })
    renderApp()

    expect(await screen.findByText('WORKER STALE')).toBeInTheDocument()
    expect(screen.getByText('System degraded')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'P-001 → P-002' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Acknowledge incident' })).toBeEnabled()
  })

  it('shows a suppressed sensor diagnostic without ticket actions', async () => {
    installFetchRouter({ incidents: [], suppressedIncidents: [suppressedIncident] })
    renderApp()

    expect(await screen.findByRole('heading', { name: 'DEV-P-002' })).toBeInTheDocument()
    expect(screen.getAllByText('SENSOR ANOMALY')).toHaveLength(3)
    expect(screen.getAllByText('SUPPRESSED')).toHaveLength(2)
    expect(screen.getByText('No dispatch ticket created')).toBeInTheDocument()
    expect(screen.getByText(/telemetry-consistency-rule/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Acknowledge incident' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run scenario' })).toBeEnabled()
  })

  it('labels inferred topology and withholds exact-span precision', async () => {
    installFetchRouter({ incidents: [inferredIncident] })
    renderApp()

    expect(await screen.findByRole('heading', { name: 'P-201 → P-202' })).toBeInTheDocument()
    expect(screen.getAllByText('PROBABLE SPAN')).toHaveLength(2)
    expect(screen.getByText('Geographically inferred topology')).toBeInTheDocument()
    expect(screen.getByText(/topology quality 84\/100/)).toBeInTheDocument()
    expect(screen.getByText(/Exact-span precision is prohibited/)).toBeInTheDocument()
  })

  it('sends the inferred-span scenario through the simulator API', async () => {
    const fetchMock = installFetchRouter({ incidents: [] })
    renderApp()

    const scenarioPicker = await screen.findByRole('combobox', { name: 'Scenario' })
    await screen.findByRole('option', { name: 'Inferred span fault' })
    fireEvent.change(scenarioPicker, {
      target: { value: 'inferred-span' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run scenario' }))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/simulator/scenarios/inferred-span/run',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
  })

  it('sends the selected feeder-fault scenario through the simulator API', async () => {
    const fetchMock = installFetchRouter({ incidents: [] })
    renderApp()

    const scenarioPicker = await screen.findByRole('combobox', { name: 'Scenario' })
    await screen.findByRole('option', { name: 'Feeder fault' })
    fireEvent.change(scenarioPicker, {
      target: { value: 'feeder-fault' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run scenario' }))

    expect(await screen.findByText(/Correlated DT-wide loss across one feeder started/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/simulator/scenarios/feeder-fault/run',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('explains corridor precision without exposing legacy injection shortcuts', async () => {
    installFetchRouter({ incidents: [corridorIncident] })
    renderApp()

    expect(await screen.findByRole('heading', { name: 'P-001 ⇢ P-003' })).toBeInTheDocument()
    expect(screen.getAllByText('CORRIDOR')).toHaveLength(2)
    expect(screen.getByText('Exact span intentionally withheld')).toBeInTheDocument()
    expect(screen.getByText('Bounded corridor: P-001 → P-002 → P-003')).toBeInTheDocument()
    expect(screen.getByText('Why Propel chose this corridor')).toBeInTheDocument()

    expect(screen.queryByRole('button', { name: 'Inject corridor fault' })).not.toBeInTheDocument()
  })

  it('maps a corridor incident back to its physical simulator fault for repair', async () => {
    sessionStorage.setItem(
      'propel-active-simulator-faults',
      JSON.stringify([
        {
          fault_id: 'fault-1',
          fault_type: 'SPAN_FAULT',
          feeder_id: 'FDR-001',
          dt_id: 'DT-001',
          parent_pole_id: 'P-001',
          child_pole_id: 'P-002',
          status: 'ACTIVE',
          deenergized_pole_ids: ['P-002', 'P-003', 'P-004'],
          injected_at: '2026-08-04T01:00:00Z',
          injection_telemetry_at: '2026-08-04T01:00:00Z',
          repaired_at: null,
          emitted_event_ids: ['event-1'],
        },
      ]),
    )
    const fetchMock = installFetchRouter({
      incidents: [corridorIncident],
      ticket: { ...ticket, status: 'RESOLVED', restoration_status: 'REPAIR_NOT_VERIFIED' },
    })
    renderApp()

    const repair = await screen.findByRole('button', {
      name: 'Complete repair',
    })
    await waitFor(() => expect(repair).toBeEnabled())
    fireEvent.click(repair)

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/simulator/faults/fault-1/repair',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
  })

  it('runs three independent faults from the minimal scenario menu', async () => {
    const fetchMock = installFetchRouter({ incidents: [] })
    renderApp()

    const scenarioPicker = await screen.findByRole('combobox', { name: 'Scenario' })
    await screen.findByRole('option', { name: 'Three simultaneous faults' })
    fireEvent.change(scenarioPicker, {
      target: { value: 'simultaneous-spans' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Run scenario' }))

    expect(await screen.findByText(/started with 3 physical faults/)).toBeInTheDocument()
    expect(screen.getByText('3 active faults')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run scenario' })).toBeDisabled()
    await waitFor(() =>
      expect(
        JSON.parse(sessionStorage.getItem('propel-active-simulator-faults') ?? '[]'),
      ).toHaveLength(3),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/simulator/scenarios/simultaneous-spans/run',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('keeps list, map, evidence, and ticket selection synchronized across incidents', async () => {
    installFetchRouter({ incidents: [incident, secondIncident] })
    renderApp()

    expect(await screen.findByTestId('network-map')).toHaveTextContent('P-001->P-002')
    const secondCardLabel = await screen.findByText('P-101 → P-102')
    fireEvent.click(secondCardLabel.closest('button') as HTMLButtonElement)

    await waitFor(() =>
      expect(screen.getByTestId('network-map')).toHaveTextContent('P-101->P-102'),
    )
    expect(screen.getByRole('heading', { name: 'P-101 → P-102' })).toBeInTheDocument()
    expect(screen.getByText('P-102')).toBeInTheDocument()
  })

  it('presents backend health failure instead of claiming data is current', async () => {
    installFetchRouter({ incidents: [], unhealthy: true })
    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('Live backend data is unavailable')
    expect(screen.getByText('System degraded')).toBeInTheDocument()
    expect(screen.getByText(/Last refresh/)).toBeInTheDocument()
  })
})
