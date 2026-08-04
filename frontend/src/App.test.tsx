import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import type {
  HealthResponse,
  Incident,
  NetworkOverview,
  NetworkPole,
  NetworkTopology,
  Ticket,
} from './api/types'

vi.mock('./components/NetworkMap', () => ({
  NetworkMap: ({
    overview,
    selectedIncident,
  }: {
    overview: NetworkOverview | null
    selectedIncident: Incident | null
  }) => (
    <div data-testid="network-map">
      Map focus: {selectedIncident?.suspected_asset_id ?? 'network'}
      <span>
        Assets: {overview?.feeder_id ?? 'none'} · {overview?.transformers.length ?? 0} DTs
      </span>
    </div>
  ),
}))

const health: HealthResponse = {
  status: 'healthy',
  service: 'propel-backend',
  dependencies: { database: { status: 'ok' }, redis: { status: 'ok' } },
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
  spans: [{ parent_pole_id: null, child_pole_id: 'P-001', source: 'SURVEYED', edge_confidence: 1 }],
}

const networkOverview: NetworkOverview = {
  feeder_id: 'FDR-001',
  name: 'Demo Feeder',
  substation: {
    substation_id: 'SUB-001',
    name: 'Demo Substation',
    latitude: 12.889,
    longitude: 77.5839,
    pin_code: '560078',
  },
  transformers: [
    {
      dt_id: 'DT-001',
      name: 'Demo DT 1',
      latitude: 12.8891,
      longitude: 77.584,
      pin_code: '560078',
    },
    {
      dt_id: 'DT-002',
      name: 'Demo DT 2',
      latitude: 12.89005,
      longitude: 77.585,
      pin_code: '560078',
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
}) {
  const request = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/health') {
      return jsonResponse(options?.unhealthy ? { ...health, status: 'unhealthy' } : health)
    }
    if (url === '/api/incidents?status=ACTIVE&limit=100') {
      return jsonResponse(options?.incidents ?? [incident])
    }
    if (url === '/api/incidents?status=SUPPRESSED&limit=100') {
      return jsonResponse(options?.suppressedIncidents ?? [])
    }
    if (url === '/api/incidents/incident-1') return jsonResponse(incident)
    if (url === '/api/incidents/incident-suppressed') return jsonResponse(suppressedIncident)
    if (url === '/api/tickets/ticket-1' && init?.method !== 'POST') return jsonResponse(ticket)
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
    if (url.startsWith('/api/network/poles')) return jsonResponse(poles)
    if (url === '/api/network/overview/FDR-001') return jsonResponse(networkOverview)
    if (url === '/api/network/topology/DT-001') return jsonResponse(topology)
    if (url === '/api/network/topology/DT-002') {
      return jsonResponse({ ...topology, dt_id: 'DT-002', spans: [] })
    }
    if (url === '/api/simulator/faults') {
      const payload = JSON.parse(String(init?.body)) as { fault_type: string }
      return jsonResponse(
        {
          fault_id: 'fault-1',
          fault_type: payload.fault_type,
          feeder_id: 'FDR-001',
          dt_id: payload.fault_type === 'FEEDER_FAULT' ? null : 'DT-001',
          parent_pole_id: null,
          child_pole_id: null,
          status: 'ACTIVE',
          deenergized_pole_ids: ['P-001'],
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
    expect(screen.getAllByText('SPAN FAULT')).toHaveLength(2)
    expect(screen.getAllByText('EXACT SPAN')).toHaveLength(2)
    expect(screen.getAllByText('3 poles')).toHaveLength(2)
    expect(
      screen.getByText('surveyed topology supports exact-span precision'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('network-map')).toHaveTextContent('P-001->P-002')
    expect(screen.getByTestId('network-map')).toHaveTextContent('FDR-001 · 2 DTs')
    expect(await screen.findByRole('button', { name: 'Acknowledge incident' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Assign crew' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Claim physical repair' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send repair telemetry' })).toBeDisabled()
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

  it('shows a healthy empty state and allows fixed-scenario injection', async () => {
    installFetchRouter({ incidents: [] })
    renderApp()

    expect(await screen.findByRole('heading', { name: 'No active outages' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'No incident selected' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Inject span fault' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Inject DT fault' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Inject feeder fault' })).toBeEnabled()
    expect(screen.getByText(/Last refresh/)).toBeInTheDocument()
  })

  it('shows a suppressed sensor diagnostic without ticket actions', async () => {
    installFetchRouter({ incidents: [], suppressedIncidents: [suppressedIncident] })
    renderApp()

    expect(await screen.findByRole('heading', { name: 'DEV-P-002' })).toBeInTheDocument()
    expect(screen.getAllByText('SENSOR ANOMALY')).toHaveLength(2)
    expect(screen.getAllByText('SUPPRESSED')).toHaveLength(2)
    expect(screen.getByText('No dispatch ticket created')).toBeInTheDocument()
    expect(screen.getByText(/telemetry-consistency-rule/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Acknowledge incident' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Inject span fault' })).toBeEnabled()
  })

  it('sends the selected feeder-fault scenario through the simulator API', async () => {
    const fetchMock = installFetchRouter({ incidents: [] })
    renderApp()

    fireEvent.click(await screen.findByRole('button', { name: 'Inject feeder fault' }))

    expect(await screen.findByText(/FEEDER FAULT injected/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/simulator/faults',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ fault_type: 'FEEDER_FAULT' }),
      }),
    )
  })

  it('presents backend health failure instead of claiming data is current', async () => {
    installFetchRouter({ incidents: [], unhealthy: true })
    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('Live backend data is unavailable')
    expect(screen.getByText('System degraded')).toBeInTheDocument()
    expect(screen.getByText(/Last refresh/)).toBeInTheDocument()
  })
})
