import type { LatLngBoundsExpression, LatLngExpression } from 'leaflet'
import { useEffect, useMemo } from 'react'
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'

import type { Incident, NetworkPole, NetworkTopology, PoleStatus } from '../api/types'

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
  topology: NetworkTopology | undefined
  selectedIncident: Incident | null
}

export function NetworkMap({ poles, topology, selectedIncident }: NetworkMapProps) {
  const polesById = useMemo(
    () => new Map(poles.map((pole) => [pole.pole_id, pole])),
    [poles],
  )
  const selectedSpan = selectedIncident?.suspected_asset_id.split('->') ?? []
  const focusPoints = useMemo<LatLngExpression[]>(() => {
    const networkPoints = poles.map(
      (pole) => [pole.latitude, pole.longitude] satisfies LatLngExpression,
    )
    if (selectedIncident) {
      networkPoints.push([selectedIncident.latitude, selectedIncident.longitude])
    }
    return networkPoints
  }, [poles, selectedIncident])

  return (
    <div className="map-frame" aria-label="Seeded DT-001 network map">
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

        {topology?.spans.map((span) => {
          if (span.parent_pole_id === null) return null
          const parent = polesById.get(span.parent_pole_id)
          const child = polesById.get(span.child_pole_id)
          if (!parent || !child) return null
          const selected =
            selectedSpan[0] === span.parent_pole_id && selectedSpan[1] === span.child_pole_id
          return (
            <Polyline
              key={`${span.parent_pole_id}-${span.child_pole_id}`}
              positions={[
                [parent.latitude, parent.longitude],
                [child.latitude, child.longitude],
              ]}
              pathOptions={{
                color: selected ? '#e75f3b' : '#446a5b',
                weight: selected ? 7 : 4,
                opacity: selected ? 1 : 0.72,
              }}
            >
              <Tooltip>
                {span.parent_pole_id} → {span.child_pole_id} · {span.source}
              </Tooltip>
            </Polyline>
          )
        })}

        {poles.map((pole) => (
          <CircleMarker
            key={pole.pole_id}
            center={[pole.latitude, pole.longitude]}
            radius={selectedIncident?.affected_pole_ids.includes(pole.pole_id) ? 10 : 8}
            pathOptions={{
              color: '#f4f1e8',
              weight: 2,
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

        {selectedIncident && (
          <CircleMarker
            center={[selectedIncident.latitude, selectedIncident.longitude]}
            radius={17}
            pathOptions={{
              color: '#ffb29e',
              weight: 3,
              fillColor: '#e75f3b',
              fillOpacity: 0.24,
            }}
          >
            <Tooltip direction="bottom" offset={[0, 16]} permanent>
              Probable fault
            </Tooltip>
          </CircleMarker>
        )}
      </MapContainer>

      <ul className="map-legend" aria-label="Pole state legend">
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
