export type TicketStatus =
  | 'DETECTED'
  | 'ACKNOWLEDGED'
  | 'CREW_ASSIGNED'
  | 'RESOLVED'
  | 'VERIFIED'
  | 'CLOSED'

export type PoleStatus = 'LIVE' | 'DARK' | 'STALE' | 'UNKNOWN' | 'NO_DEVICE'

export interface Incident {
  incident_id: string
  fingerprint: string
  status: 'ACTIVE' | 'RESOLVED' | 'SUPPRESSED'
  classification: string
  suspected_asset_type: string
  suspected_asset_id: string
  latitude: number
  longitude: number
  pin_code: string | null
  affected_pole_count: number
  affected_pole_ids: string[]
  precision: string
  confidence_score: number
  confidence_reason: string
  evidence: Record<string, unknown>
  suppression_reason: string | null
  suppression_source: string | null
  suppression_external_id: string | null
  detected_at: string
  updated_at: string
  resolved_at: string | null
  ticket_id: string | null
  ticket_status: TicketStatus | null
  assigned_crew: string | null
}

export interface TicketEvent {
  from_status: TicketStatus | null
  to_status: TicketStatus
  actor: string
  reason: string | null
  occurred_at: string
  details: Record<string, unknown>
}

export interface Ticket {
  ticket_id: string
  incident_id: string
  status: TicketStatus
  assigned_crew: string | null
  created_at: string
  updated_at: string
  resolution_claimed_at: string | null
  verified_at: string | null
  closed_at: string | null
  restoration_status: string | null
  remaining_dark_count: number | null
  events: TicketEvent[]
}

export interface NetworkPole {
  pole_id: string
  dt_id: string
  latitude: number
  longitude: number
  pin_code: string
  state: PoleStatus
  state_received_at: string | null
  device_id: string | null
}

export interface NetworkSpan {
  parent_pole_id: string | null
  child_pole_id: string
  source: 'SURVEYED' | 'INFERRED'
  edge_confidence: number
  distance_m: number
  inference_version: string | null
}

export interface NetworkTopology {
  dt_id: string
  topology_version: number
  source: 'SURVEYED' | 'INFERRED' | null
  quality_score: number
  quality_tier: string
  quality_reasons: string[]
  inference_version: string | null
  spans: NetworkSpan[]
}

export interface NetworkSubstation {
  substation_id: string
  name: string
  latitude: number
  longitude: number
  pin_code: string
}

export interface NetworkTransformer {
  dt_id: string
  name: string
  latitude: number
  longitude: number
  pin_code: string
}

export interface NetworkOverview {
  feeder_id: string
  name: string
  substation: NetworkSubstation
  transformers: NetworkTransformer[]
}

export interface NetworkFeeder {
  feeder_id: string
  name: string
  substation_id: string
}

export interface NetworkSubdivisionTransformer extends NetworkTransformer {
  feeder_id: string
}

export interface NetworkBounds {
  south: number
  west: number
  north: number
  east: number
}

export interface NetworkSubdivision {
  dataset_id: string
  generator_version: string
  name: string
  neighborhoods: string[]
  bounds: NetworkBounds
  substations: NetworkSubstation[]
  feeders: NetworkFeeder[]
  transformers: NetworkSubdivisionTransformer[]
  topologies: NetworkTopology[]
}

export interface SimulatedFault {
  fault_id: string
  fault_type: 'SPAN_FAULT' | 'DT_FAULT' | 'FEEDER_FAULT'
  feeder_id: string | null
  dt_id: string | null
  parent_pole_id: string | null
  child_pole_id: string | null
  status: 'ACTIVE' | 'REPAIRED'
  deenergized_pole_ids: string[]
  injected_at: string
  injection_telemetry_at: string | null
  repaired_at: string | null
  emitted_event_ids: string[]
  restored_pole_ids: string[]
  restoration_fraction: number | null
}

export interface InjectFaultRequest {
  fault_type: SimulatedFault['fault_type']
  feeder_id?: string
  dt_id?: string
  parent_pole_id?: string
  child_pole_id?: string
  missing_device_pole_ids?: string[]
  omit_loss_pole_ids?: string[]
  duplicate_loss_pole_ids?: string[]
  delayed_loss_pole_ids?: string[]
  out_of_order_pole_ids?: string[]
}

export interface SimulatorScenario {
  scenario_id: string
  description: string
  fault_count: number
  scheduled: boolean
  restoration_fraction: number
  noise_modes: string[]
}

export interface SimulatorScenarioRun {
  scenario_id: string
  description: string
  faults: SimulatedFault[]
  restoration_fraction: number
  failed_device_id: string | null
  failed_pole_id: string | null
  scheduled_outage_id: string | null
}

export interface HealthResponse {
  status: 'healthy' | 'unhealthy'
  service: string
  dependencies: Record<string, { status: string }>
}

export interface DiagnosticWarning {
  code: string
  severity: 'critical' | 'warning' | 'info'
  message: string
}

export interface OperationalDiagnostics {
  status: 'healthy' | 'degraded'
  generated_at: string
  dependencies: Record<string, { status: 'ok' | 'unavailable' }>
  worker: { status: 'ok' | 'stale' | 'unknown'; last_seen_at: string | null }
  queue: {
    stream_length: number | null
    pending: number | null
    lag: number | null
    dead_letter_count: number | null
    analysis_pending: number | null
    analysis_overdue: number | null
  }
  device_counts: Record<string, number>
  pole_state_counts: Record<string, number>
  incident_counts: Record<string, number>
  latest_processed_at: string | null
  warnings: DiagnosticWarning[]
}

export interface TelemetryDiagnostic {
  event_id: string
  correlation_id: string
  device_id: string
  pole_id: string
  event_type: string
  energized: boolean
  device_timestamp: string
  received_at: string
  processed_at: string
  sequence: number
  processing_outcome: string
  origin: string
  state_changed: boolean
}

export interface TelemetryDiagnosticPage {
  items: TelemetryDiagnostic[]
  next_cursor: string | null
}

export interface DeviceHealthDiagnostic {
  device_id: string
  pole_id: string | null
  dt_id: string | null
  status: string
  pole_state: string
  last_seen_at: string | null
  last_sequence: number | null
  last_event_type: string | null
  firmware: string | null
  battery_mv: number | null
  rssi: number | null
  status_reason: string
  can_report_power_loss: boolean
}

export interface DeviceHealthDiagnosticPage {
  items: DeviceHealthDiagnostic[]
  next_cursor: string | null
}

export interface ApiErrorBody {
  error?: {
    code?: string
    message?: string
    retryable?: boolean
  }
}
