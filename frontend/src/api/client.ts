import type {
  ApiErrorBody,
  HealthResponse,
  InjectFaultRequest,
  Incident,
  NetworkOverview,
  NetworkPole,
  NetworkSubdivision,
  SimulatedFault,
  Ticket,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean

  constructor(status: number, body: ApiErrorBody) {
    super(body.error?.message ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.code = body.error?.code ?? 'REQUEST_FAILED'
    this.retryable = body.error?.retryable ?? status >= 500
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body !== undefined) {
    headers.set('content-type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    let body: ApiErrorBody = {}
    try {
      body = (await response.json()) as ApiErrorBody
    } catch {
      // A stable fallback keeps proxy and infrastructure failures operator-visible.
    }
    throw new ApiError(response.status, body)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => requestJson<HealthResponse>('/health'),
  incidents: () => requestJson<Incident[]>('/api/incidents?status=ACTIVE&limit=100'),
  suppressedIncidents: () =>
    requestJson<Incident[]>('/api/incidents?status=SUPPRESSED&limit=100'),
  incident: (incidentId: string) =>
    requestJson<Incident>(`/api/incidents/${incidentId}`),
  ticket: (ticketId: string) => requestJson<Ticket>(`/api/tickets/${ticketId}`),
  subdivisionPoles: () =>
    requestJson<NetworkPole[]>('/api/network/subdivision/poles'),
  subdivision: () =>
    requestJson<NetworkSubdivision>('/api/network/subdivision'),
  networkOverview: () =>
    requestJson<NetworkOverview>('/api/network/overview/FDR-001'),
  acknowledge: (ticketId: string) =>
    requestJson<Ticket>(`/api/tickets/${ticketId}/acknowledge`, {
      method: 'POST',
      body: JSON.stringify({ actor: 'operator-console', reason: 'Alarm reviewed' }),
    }),
  assign: (ticketId: string, assignedCrew: string) =>
    requestJson<Ticket>(`/api/tickets/${ticketId}/assign`, {
      method: 'POST',
      body: JSON.stringify({
        actor: 'operator-console',
        assigned_crew: assignedCrew,
        reason: 'Crew dispatched from operator console',
      }),
    }),
  resolve: (ticketId: string) =>
    requestJson<Ticket>(`/api/tickets/${ticketId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({
        actor: 'operator-console',
        reason: 'Crew reports physical repair complete',
      }),
    }),
  injectFault: (request: InjectFaultRequest) =>
    requestJson<SimulatedFault>('/api/simulator/faults', {
      method: 'POST',
      body: JSON.stringify(request),
    }),
  repairFault: (faultId: string) =>
    requestJson<SimulatedFault>(`/api/simulator/faults/${faultId}/repair`, {
      method: 'POST',
    }),
  resetSimulator: () =>
    requestJson<{ status: 'reset'; repaired_faults: SimulatedFault[] }>(
      '/api/simulator/reset',
      { method: 'POST' },
    ),
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.message} (${error.code})`
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'The request failed unexpectedly.'
}
