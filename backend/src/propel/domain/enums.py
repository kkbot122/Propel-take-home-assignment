from enum import StrEnum


class FaultClass(StrEnum):
    SPAN_FAULT = "SPAN_FAULT"
    DT_FAULT = "DT_FAULT"
    FEEDER_FAULT = "FEEDER_FAULT"
    SENSOR_ANOMALY = "SENSOR_ANOMALY"
    SCHEDULED_OUTAGE = "SCHEDULED_OUTAGE"
    UNCONFIRMED_OUTAGE = "UNCONFIRMED_OUTAGE"


class LocalizationPrecision(StrEnum):
    POLE_LEVEL = "POLE_LEVEL"
    EXACT_SPAN = "EXACT_SPAN"
    PROBABLE_SPAN = "PROBABLE_SPAN"
    CORRIDOR = "CORRIDOR"
    DT_LEVEL = "DT_LEVEL"
    FEEDER_LEVEL = "FEEDER_LEVEL"


class TicketStatus(StrEnum):
    DETECTED = "DETECTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CREW_ASSIGNED = "CREW_ASSIGNED"
    RESOLVED = "RESOLVED"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"


class PoleStatus(StrEnum):
    LIVE = "LIVE"
    DARK = "DARK"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    NO_DEVICE = "NO_DEVICE"


class DeviceHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class TopologySource(StrEnum):
    SURVEYED = "SURVEYED"
    INFERRED = "INFERRED"


class TelemetryEventType(StrEnum):
    HEARTBEAT = "heartbeat"
    POWER_LOST = "power_lost"
    POWER_RESTORED = "power_restored"
    BOOT = "boot"


class TelemetryOrigin(StrEnum):
    DEVICE = "DEVICE"
    SIMULATOR = "SIMULATOR"


class SimulatorFaultStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REPAIRED = "REPAIRED"


class ProcessingOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    STALE = "stale"
    INVALID = "invalid"
    QUARANTINED = "quarantined"


class IncidentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


class SuspectedAssetType(StrEnum):
    DEVICE = "DEVICE"
    SPAN = "SPAN"
    DISTRIBUTION_TRANSFORMER = "DISTRIBUTION_TRANSFORMER"
    FEEDER = "FEEDER"


class ScheduledOutageScope(StrEnum):
    SPAN = "SPAN"
    DISTRIBUTION_TRANSFORMER = "DISTRIBUTION_TRANSFORMER"
    FEEDER = "FEEDER"
