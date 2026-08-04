import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import type {
  HealthResponse,
  Incident,
  NetworkPole,
  NetworkTopology,
  Ticket,
} from './api/types'

vi.mock('./components/NetworkMap', () => ({
  NetworkMap: ({ selectedIncident }: { selectedIncident: Incident | null }) => (
    <div data-testid="network-map">
      Map focus: {selectedIncident?.suspected_asset_id ?? 'network'}
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
  detected_at: '2026-08-04T01:00:00Z',
  updated_at: '2026-08-04T01:00:00Z',
  resolved_at: null,
  ticket_id: 'ticket-1',
  ticket_status: 'DETECTED',
  assigned_crew: null,
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function installFetchRouter(options?: { incidents?: Incident[]; unhealthy?: boolean }) {
  const request = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url === '/health') {
      return jsonResponse(options?.unhealthy ? { ...health, status: 'unhealthy' } : health)
    }
    if (url.startsWith('/api/incidents?')) return jsonResponse(options?.incidents ?? [incident])
    if (url === '/api/incidents/incident-1') return jsonResponse(incident)
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
    if (url === '/api/network/topology/DT-001') return jsonResponse(topology)
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
    expect(screen.getByRole('button', { name: 'Inject fixed fault' })).toBeEnabled()
    expect(screen.getByText(/Last refresh/)).toBeInTheDocument()
  })

  it('presents backend health failure instead of claiming data is current', async () => {
    installFetchRouter({ incidents: [], unhealthy: true })
    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent('Live backend data is unavailable')
    expect(screen.getByText('System degraded')).toBeInTheDocument()
    expect(screen.getByText(/Last refresh/)).toBeInTheDocument()
  })
})
