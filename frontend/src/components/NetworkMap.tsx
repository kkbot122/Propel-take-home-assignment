import { divIcon } from 'leaflet'
import type { LatLngBoundsExpression, LatLngExpression } from 'leaflet'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from 'react-leaflet'

import type {
  Incident,
  NetworkPole,
  NetworkSubdivision,
  PoleStatus,
} from '../api/types'

const DEFAULT_CENTER: LatLngExpression = [12.872, 77.584]
const DEFAULT_SUBDIVISION_BOUNDS: LatLngBoundsExpression = [
  [12.826, 77.552],
  [12.917, 77.62],
]
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
  html: '<span>S</span>',
  iconAnchor: [14, 14],
  iconSize: [28, 28],
})
const selectedFeederSourceIcon = divIcon({
  className: 'asset-marker asset-marker-feeder selected',
  html: '<span>S</span>',
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
  const pointsRef = useRef(points)

  useEffect(() => {
    pointsRef.current = points
  }, [points])

  useEffect(() => {
    if (pointsRef.current.length === 0) return
    map.fitBounds(pointsRef.current as LatLngBoundsExpression, {
      padding: [36, 36],
      maxZoom: 17,
    })
  }, [focusKey, map])

  return null
}

interface MapViewport {
  zoom: number
  south: number
  west: number
  north: number
  east: number
}

function MapViewportObserver({ onChange }: { onChange: (viewport: MapViewport) => void }) {
  const map = useMap()
  const update = useCallback(() => {
    const bounds = map.getBounds().pad(0.12)
    onChange({
      zoom: map.getZoom(),
      south: bounds.getSouth(),
      west: bounds.getWest(),
      north: bounds.getNorth(),
      east: bounds.getEast(),
    })
  }, [map, onChange])

  useMapEvents({
    moveend: update,
    zoomend: update,
  })
  useEffect(update, [update])
  return null
}

function poleIsVisible(pole: NetworkPole, viewport: MapViewport): boolean {
  return (
    pole.latitude >= viewport.south &&
    pole.latitude <= viewport.north &&
    pole.longitude >= viewport.west &&
    pole.longitude <= viewport.east
  )
}

interface NetworkMapProps {
  poles: NetworkPole[]
  subdivision: NetworkSubdivision | null
  selectedIncident: Incident | null
  showPoleLabels: boolean
}

export function NetworkMap({
  poles,
  subdivision,
  selectedIncident,
  showPoleLabels,
}: NetworkMapProps) {
  const [viewport, setViewport] = useState<MapViewport | null>(null)
  const updateViewport = useCallback((nextViewport: MapViewport) => {
    setViewport((current) => {
      if (
        current?.zoom === nextViewport.zoom &&
        current.south === nextViewport.south &&
        current.west === nextViewport.west &&
        current.north === nextViewport.north &&
        current.east === nextViewport.east
      ) {
        return current
      }
      return nextViewport
    })
  }, [])
  const topologies = useMemo(() => subdivision?.topologies ?? [], [subdivision?.topologies])
  const polesById = useMemo(
    () => new Map(poles.map((pole) => [pole.pole_id, pole])),
    [poles],
  )
  const transformersById = useMemo(
    () => new Map(subdivision?.transformers.map((item) => [item.dt_id, item])),
    [subdivision],
  )
  const feedersById = useMemo(
    () => new Map(subdivision?.feeders.map((item) => [item.feeder_id, item])),
    [subdivision],
  )
  const substationsById = useMemo(
    () => new Map(subdivision?.substations.map((item) => [item.substation_id, item])),
    [subdivision],
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
    if (selectedIncident) {
      const incidentPoints = selectedIncident.affected_pole_ids
        .map((poleId) => polesById.get(poleId))
        .filter((pole): pole is NetworkPole => pole !== undefined)
        .map((pole) => [pole.latitude, pole.longitude] satisfies LatLngExpression)
      incidentPoints.push([selectedIncident.latitude, selectedIncident.longitude])
      return incidentPoints
    }
    const networkPoints = poles.map(
      (pole) => [pole.latitude, pole.longitude] satisfies LatLngExpression,
    )
    if (subdivision) {
      networkPoints.push(
        ...subdivision.substations.map(
          (substation) =>
            [substation.latitude, substation.longitude] satisfies LatLngExpression,
        ),
      )
      networkPoints.push(
        ...subdivision.transformers.map(
          (transformer) =>
            [transformer.latitude, transformer.longitude] satisfies LatLngExpression,
        ),
      )
    }
    return networkPoints
  }, [poles, polesById, selectedIncident, subdivision])
  const detailMode = showPoleLabels || selectedIncident !== null || (viewport?.zoom ?? 13) >= 15
  const renderedPoles = useMemo(() => {
    if (!detailMode) return []
    if (showPoleLabels) return poles
    if (viewport) return poles.filter((pole) => poleIsVisible(pole, viewport))
    if (selectedIncident) {
      const affectedPoleIds = new Set(selectedIncident.affected_pole_ids)
      return poles.filter((pole) => affectedPoleIds.has(pole.pole_id))
    }
    return []
  }, [detailMode, poles, selectedIncident, showPoleLabels, viewport])
  const renderedPoleIds = useMemo(
    () => new Set(renderedPoles.map((pole) => pole.pole_id)),
    [renderedPoles],
  )
  const renderedTopologies = useMemo(
    () =>
      detailMode
        ? topologies.map((topology) => ({
            ...topology,
            spans: topology.spans.filter(
              (span) =>
                renderedPoleIds.has(span.child_pole_id) ||
                (span.parent_pole_id !== null && renderedPoleIds.has(span.parent_pole_id)),
            ),
          }))
        : [],
    [detailMode, renderedPoleIds, topologies],
  )
  const selectedFeederId =
    selectedIncident?.suspected_asset_type === 'FEEDER'
      ? selectedIncident.suspected_asset_id
      : null
  const selectedTransformerId =
    selectedIncident?.suspected_asset_type === 'DISTRIBUTION_TRANSFORMER'
      ? selectedIncident.suspected_asset_id
      : null
  const faultFocusLabel = selectedIncident
    ? `${selectedIncident.classification.replaceAll('_', ' ')} focus`
    : null
  const mapBounds: LatLngBoundsExpression = subdivision
    ? [
        [subdivision.bounds.south, subdivision.bounds.west],
        [subdivision.bounds.north, subdivision.bounds.east],
      ]
    : DEFAULT_SUBDIVISION_BOUNDS
  const networkFocusKey = `${selectedIncident?.incident_id ?? 'network'}:${
    subdivision?.transformers.map((item) => item.dt_id).join(',') ?? 'empty'
  }`

  return (
    <div
      className="map-frame"
      aria-label="South Bengaluru subdivision network map"
      data-map-zoom={viewport?.zoom ?? 13}
      data-rendered-poles={renderedPoles.length}
    >
      <MapContainer
        center={DEFAULT_CENTER}
        zoom={13}
        minZoom={12}
        maxZoom={18}
        maxBounds={mapBounds}
        maxBoundsViscosity={1}
        preferCanvas
        zoomSnap={0.25}
        className="network-map"
        scrollWheelZoom
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url={TILE_URL}
        />
        <FitNetwork
          points={focusPoints}
          focusKey={networkFocusKey}
        />
        <MapViewportObserver onChange={updateViewport} />

        {subdivision?.transformers.map((transformer) => {
          const feeder = feedersById.get(transformer.feeder_id)
          const substation = feeder ? substationsById.get(feeder.substation_id) : null
          if (!feeder || !substation) return null
          const selected = selectedFeederId === feeder.feeder_id
          return (
            <Polyline
              key={`${feeder.feeder_id}-${transformer.dt_id}`}
              positions={[
                [substation.latitude, substation.longitude],
                [transformer.latitude, transformer.longitude],
              ]}
              pathOptions={{
                color: selected ? '#b6321b' : '#735a92',
                weight: selected ? 5 : showPoleLabels ? 2.5 : 1.25,
                opacity: selected ? 1 : 0.58,
                dashArray: selected ? '12 7' : '8 7',
              }}
            >
              <Tooltip sticky>
                {feeder.name} · source to {transformer.name}
              </Tooltip>
            </Polyline>
          )
        })}

        {renderedTopologies.flatMap((topology) =>
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
                weight: selected ? 7 : showPoleLabels ? 2.5 : 1,
                opacity: selected ? 1 : inferred ? 0.68 : 0.52,
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

        {subdivision?.substations.map((substation) => {
          const substationFeeders = subdivision.feeders.filter(
            (feeder) => feeder.substation_id === substation.substation_id,
          )
          const selected = substationFeeders.some(
            (feeder) => feeder.feeder_id === selectedFeederId,
          )
          return (
            <Marker
              key={substation.substation_id}
              position={[substation.latitude, substation.longitude]}
              icon={selected ? selectedFeederSourceIcon : feederSourceIcon}
              zIndexOffset={500}
            >
              <Tooltip className="asset-tooltip" permanent direction="left" offset={[-10, 0]}>
                {substation.name}
              </Tooltip>
              <Popup>
                <strong>{substation.name}</strong>
                <br />
                {substation.substation_id} · PIN {substation.pin_code}
                <br />
                Feeders: {substationFeeders.map((feeder) => feeder.name).join(', ')}
              </Popup>
            </Marker>
          )
        })}

        {subdivision?.transformers.map((transformer) => {
          const selected =
            transformer.dt_id === selectedTransformerId ||
            transformer.feeder_id === selectedFeederId
          return (
            <Marker
              key={transformer.dt_id}
              position={[transformer.latitude, transformer.longitude]}
              icon={selected ? selectedTransformerIcon : transformerIcon}
              zIndexOffset={400}
            >
              <Tooltip
                className="asset-tooltip"
                permanent={showPoleLabels || selected}
                direction="top"
                offset={[0, -11]}
              >
                {transformer.name}
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

        {renderedPoles.map((pole) => {
          const affected = selectedIncident?.affected_pole_ids.includes(pole.pole_id) ?? false
          return (
            <CircleMarker
              key={pole.pole_id}
              center={[pole.latitude, pole.longitude]}
              radius={affected ? 9 : showPoleLabels ? 5 : 2.5}
              pathOptions={{
                color: affected ? '#b6321b' : poleColors[pole.state],
                weight: affected ? 4 : showPoleLabels ? 1 : 0,
                fillColor: poleColors[pole.state],
                fillOpacity: showPoleLabels ? 0.95 : 0.78,
              }}
            >
              <Tooltip permanent={showPoleLabels || affected} direction="top" offset={[0, -8]}>
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
          )
        })}
      </MapContainer>

      {!detailMode && (
        <div className="map-detail-hint" role="status">
          Zoom in or choose a feeder/DT to reveal poles and spans
        </div>
      )}

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
