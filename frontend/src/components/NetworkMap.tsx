import { divIcon } from 'leaflet'
import type { LatLngBoundsExpression, LatLngExpression } from 'leaflet'
import { useEffect, useMemo } from 'react'
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'

import type {
  Incident,
  NetworkOverview,
  NetworkPole,
  NetworkTopology,
  PoleStatus,
} from '../api/types'

const DEFAULT_CENTER: LatLngExpression = [12.88952, 77.58433]
const TILE_URL =
  import.meta.env.VITE_OSM_TILE_URL ?? 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'

const poleColors: Record<PoleStatus, string> = {
  LIVE: '#24a66a',
  DARK: '#d94f4f',
  STALE: '#d69b2d',
  UNKNOWN: '#7f8c86',
  NO_DEVICE: '#66706c',
}

const feederSourceIcon = divIcon({
  className: 'asset-marker asset-marker-feeder',
  html: '<span>F</span>',
  iconAnchor: [14, 14],
  iconSize: [28, 28],
})
const selectedFeederSourceIcon = divIcon({
  className: 'asset-marker asset-marker-feeder selected',
  html: '<span>F</span>',
  iconAnchor: [16, 16],
  iconSize: [32, 32],
})
const transformerIcon = divIcon({
  className: 'asset-marker asset-marker-dt',
  html: '<span>DT</span>',
  iconAnchor: [14, 14],
  iconSize: [28, 28],
})
const selectedTransformerIcon = divIcon({
  className: 'asset-marker asset-marker-dt selected',
  html: '<span>DT</span>',
  iconAnchor: [16, 16],
  iconSize: [32, 32],
})

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function corridorPoleIds(incident: Incident | null): string[] {
  const candidate = incident?.evidence.candidate
  if (!isRecord(candidate)) return []
  const corridor = candidate.corridor
  if (!isRecord(corridor)) return []
  const orderedPoleIds = corridor.ordered_pole_ids
  return Array.isArray(orderedPoleIds)
    ? orderedPoleIds.filter((poleId): poleId is string => typeof poleId === 'string')
    : []
}

interface FitNetworkProps {
  points: LatLngExpression[]
  focusKey: string
}

function FitNetwork({ points, focusKey }: FitNetworkProps) {
  const map = useMap()
  const serializedPoints = JSON.stringify(points)

  useEffect(() => {
    if (points.length === 0) return
    map.fitBounds(points as LatLngBoundsExpression, {
      padding: [36, 36],
      maxZoom: 17,
    })
  }, [focusKey, map, points, serializedPoints])

  return null
}

interface NetworkMapProps {
  poles: NetworkPole[]
  topologies: NetworkTopology[]
  overview: NetworkOverview | null
  selectedIncident: Incident | null
}

export function NetworkMap({ poles, topologies, overview, selectedIncident }: NetworkMapProps) {
  const polesById = useMemo(
    () => new Map(poles.map((pole) => [pole.pole_id, pole])),
    [poles],
  )
  const transformersById = useMemo(
    () => new Map(overview?.transformers.map((transformer) => [transformer.dt_id, transformer])),
    [overview],
  )
  const selectedSpan =
    selectedIncident?.suspected_asset_type === 'SPAN' &&
    (selectedIncident.precision === 'EXACT_SPAN' ||
      selectedIncident.precision === 'PROBABLE_SPAN')
      ? selectedIncident.suspected_asset_id.split('->')
      : []
  const selectedCorridorPoleIds = corridorPoleIds(selectedIncident)
  const selectedCorridorPositions = selectedCorridorPoleIds
    .map((poleId) => polesById.get(poleId))
    .filter((pole): pole is NetworkPole => pole !== undefined)
    .map((pole) => [pole.latitude, pole.longitude] satisfies LatLngExpression)
  const focusPoints = useMemo<LatLngExpression[]>(() => {
    const networkPoints = poles.map(
      (pole) => [pole.latitude, pole.longitude] satisfies LatLngExpression,
    )
    if (overview) {
      networkPoints.push([overview.substation.latitude, overview.substation.longitude])
      networkPoints.push(
        ...overview.transformers.map(
          (transformer) =>
            [transformer.latitude, transformer.longitude] satisfies LatLngExpression,
        ),
      )
    }
    if (selectedIncident) {
      networkPoints.push([selectedIncident.latitude, selectedIncident.longitude])
    }
    return networkPoints
  }, [overview, poles, selectedIncident])
  const feederSelected =
    selectedIncident?.suspected_asset_type === 'FEEDER' &&
    selectedIncident.suspected_asset_id === overview?.feeder_id
  const selectedTransformerId =
    selectedIncident?.suspected_asset_type === 'DISTRIBUTION_TRANSFORMER'
      ? selectedIncident.suspected_asset_id
      : null
  const faultFocusLabel = selectedIncident
    ? `${selectedIncident.classification.replaceAll('_', ' ')} focus`
    : null

  return (
    <div className="map-frame" aria-label="Seeded FDR-001 network map">
      <MapContainer
        center={DEFAULT_CENTER}
        zoom={16}
        className="network-map"
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url={TILE_URL}
        />
        <FitNetwork
          points={focusPoints}
          focusKey={selectedIncident?.incident_id ?? 'network'}
        />

        {overview?.transformers.map((transformer) => (
          <Polyline
            key={`${overview.feeder_id}-${transformer.dt_id}`}
            positions={[
              [overview.substation.latitude, overview.substation.longitude],
              [transformer.latitude, transformer.longitude],
            ]}
            pathOptions={{
              color: feederSelected ? '#b6321b' : '#735a92',
              weight: feederSelected ? 7 : 4,
              opacity: feederSelected ? 1 : 0.8,
              dashArray: feederSelected ? '12 7' : '8 7',
            }}
          >
            <Tooltip sticky>
              {overview.feeder_id} · source to {transformer.dt_id}
            </Tooltip>
          </Polyline>
        ))}

        {topologies.flatMap((topology) =>
          topology.spans.map((span) => ({ span, topology })),
        ).map(({ span, topology }) => {
          const transformer = transformersById.get(topology.dt_id)
          const parent =
            span.parent_pole_id === null ? transformer : polesById.get(span.parent_pole_id)
          const child = polesById.get(span.child_pole_id)
          if (!parent || !child) return null
          const selected =
            selectedSpan[0] === span.parent_pole_id && selectedSpan[1] === span.child_pole_id
          const inferred = span.source === 'INFERRED'
          return (
            <Polyline
              key={`${span.parent_pole_id}-${span.child_pole_id}`}
              positions={[
                [parent.latitude, parent.longitude],
                [child.latitude, child.longitude],
              ]}
              pathOptions={{
                color: selected ? '#e75f3b' : inferred ? '#3979a8' : '#446a5b',
                weight: selected ? 7 : 4,
                opacity: selected ? 1 : inferred ? 0.88 : 0.72,
                dashArray: inferred ? (selected ? '8 5' : '6 6') : undefined,
              }}
            >
              <Tooltip>
                {span.parent_pole_id ?? topology.dt_id} → {span.child_pole_id} · {span.source} ·{' '}
                {(span.edge_confidence * 100).toFixed(0)}% edge score
              </Tooltip>
            </Polyline>
          )
        })}

        {selectedCorridorPositions.length >= 2 && (
          <Polyline
            positions={selectedCorridorPositions}
            pathOptions={{
              color: '#d98220',
              weight: 9,
              opacity: 0.92,
              dashArray: '7 8',
            }}
          >
            <Tooltip>
              Uncertain corridor · {selectedCorridorPoleIds.join(' → ')}
            </Tooltip>
          </Polyline>
        )}

        {selectedIncident && (
          <CircleMarker
            center={[selectedIncident.latitude, selectedIncident.longitude]}
            radius={
              selectedIncident.suspected_asset_type === 'FEEDER'
                ? 34
                : selectedIncident.suspected_asset_type === 'DISTRIBUTION_TRANSFORMER'
                  ? 28
                  : 22
            }
            pathOptions={{
              color: selectedIncident.precision === 'DT_LEVEL' ? '#71519b' : '#a92f1e',
              weight: 5,
              fillColor: selectedIncident.precision === 'DT_LEVEL' ? '#9b79c6' : '#f06b49',
              fillOpacity: 0.12,
              dashArray: '9 5',
            }}
          >
            <Tooltip className="fault-tooltip" direction="bottom" offset={[0, 24]} permanent>
              {selectedIncident.status === 'SUPPRESSED'
                ? 'Suppressed diagnostic'
                : selectedIncident.precision === 'DT_LEVEL'
                  ? 'Location degraded to DT level'
                  : faultFocusLabel}
            </Tooltip>
          </CircleMarker>
        )}

        {overview && (
          <Marker
            position={[overview.substation.latitude, overview.substation.longitude]}
            icon={feederSelected ? selectedFeederSourceIcon : feederSourceIcon}
            zIndexOffset={500}
          >
            <Tooltip className="asset-tooltip" permanent direction="left" offset={[-10, 0]}>
              {overview.feeder_id} source
            </Tooltip>
            <Popup>
              <strong>{overview.feeder_id}</strong> · {overview.name}
              <br />
              Source: {overview.substation.substation_id} · {overview.substation.name}
              <br />
              PIN: {overview.substation.pin_code}
            </Popup>
          </Marker>
        )}

        {overview?.transformers.map((transformer) => {
          const selected = transformer.dt_id === selectedTransformerId || feederSelected
          return (
            <Marker
              key={transformer.dt_id}
              position={[transformer.latitude, transformer.longitude]}
              icon={selected ? selectedTransformerIcon : transformerIcon}
              zIndexOffset={400}
            >
              <Tooltip className="asset-tooltip" permanent direction="top" offset={[0, -11]}>
                {transformer.dt_id}
              </Tooltip>
              <Popup>
                <strong>{transformer.dt_id}</strong> · {transformer.name}
                <br />
                Distribution transformer
                <br />
                PIN: {transformer.pin_code}
              </Popup>
            </Marker>
          )
        })}

        {poles.map((pole) => (
          <CircleMarker
            key={pole.pole_id}
            center={[pole.latitude, pole.longitude]}
            radius={selectedIncident?.affected_pole_ids.includes(pole.pole_id) ? 10 : 8}
            pathOptions={{
              color: selectedIncident?.affected_pole_ids.includes(pole.pole_id)
                ? '#b6321b'
                : '#f4f1e8',
              weight: selectedIncident?.affected_pole_ids.includes(pole.pole_id) ? 4 : 2,
              fillColor: poleColors[pole.state],
              fillOpacity: 1,
            }}
          >
            <Tooltip permanent direction="top" offset={[0, -8]}>
              {pole.pole_id}
            </Tooltip>
            <Popup>
              <strong>{pole.pole_id}</strong>
              <br />
              State: {pole.state}
              <br />
              Device: {pole.device_id ?? 'No device'}
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>

      <ul className="map-legend" aria-label="Network asset and pole state legend">
        <li>
          <span className="legend-feeder" aria-hidden="true" />
          FEEDER
        </li>
        <li>
          <span className="legend-dt" aria-hidden="true" />
          DT
        </li>
        <li>
          <span className="legend-inferred" aria-hidden="true" />
          INFERRED
        </li>
        {(['LIVE', 'DARK', 'STALE', 'UNKNOWN'] as PoleStatus[]).map((state) => (
          <li key={state}>
            <span style={{ background: poleColors[state] }} aria-hidden="true" />
            {state}
          </li>
        ))}
      </ul>
    </div>
  )
}
