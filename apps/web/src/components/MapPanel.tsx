import {useEffect, useRef, useState} from 'react'
import maplibregl from 'maplibre-gl'
import type {GeoJSONSource, Map as LibreMap, Marker} from 'maplibre-gl'
import {Expand, MapPin, Navigation2, WifiOff} from 'lucide-react'
import 'maplibre-gl/dist/maplibre-gl.css'
import type {Area, Run} from '../types'
import {candidateFor} from '../types'

function areaShape(area: Area): GeoJSON.FeatureCollection {
  return {type: 'FeatureCollection', features: [{type: 'Feature', properties: {}, geometry: {type: 'Polygon', coordinates: [[
    [area.west, area.south], [area.east, area.south], [area.east, area.north], [area.west, area.north], [area.west, area.south],
  ]]}}]}
}

export default function MapPanel({run, selectedId, chosenId, onSelect, onChoose, onViewport}: {
  run: Run; selectedId: string; chosenId: string | null; onSelect: (id: string) => void
  onChoose: (id: string) => void; onViewport: (area: Area) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const instance = useRef<LibreMap | null>(null)
  const markers = useRef<Marker[]>([])
  const [loaded, setLoaded] = useState(false)
  const [unavailable, setUnavailable] = useState(false)
  const [tilesError, setTilesError] = useState(false)
  const viewportCallback = useRef(onViewport)
  viewportCallback.current = onViewport
  const areaKey = JSON.stringify(run.area)

  useEffect(() => {
    let map: LibreMap | null = null
    let cancelled = false
    const timeout = setTimeout(() => {if (!cancelled && !map?.isStyleLoaded()) setUnavailable(true)}, 15000)
    try {
      map = new maplibregl.Map({container: container.current!, style: 'https://tiles.openfreemap.org/styles/dark',
        center: [79.836, 7.21], zoom: 14, attributionControl: false, pitchWithRotate: false, dragRotate: false})
      instance.current = map
      map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-right')
      map.addControl(new maplibregl.AttributionControl({compact: true}), 'bottom-right')
      map.addControl(new maplibregl.ScaleControl({maxWidth: 85, unit: 'metric'}), 'bottom-left')
      map.on('load', () => {
        if (cancelled || !map) return
        clearTimeout(timeout); setLoaded(true); setUnavailable(false)
        map.addSource('chosen-area', {type: 'geojson', data: areaShape(run.area)})
        map.addLayer({id: 'chosen-area-line', type: 'line', source: 'chosen-area', paint: {'line-color': '#a8c48d', 'line-width': 1, 'line-opacity': .4, 'line-dasharray': [4, 5]}})
        map.fitBounds([[run.area.west, run.area.south], [run.area.east, run.area.north]], {padding: 40, duration: 0})
      })
      map.on('error', () => {if (!cancelled) setTilesError(true)})
      map.on('idle', () => {if (!cancelled && map?.areTilesLoaded()) setTilesError(false)})
      map.on('moveend', () => {
        if (!map) return
        const bounds = map.getBounds()
        viewportCallback.current({west: bounds.getWest(), south: bounds.getSouth(), east: bounds.getEast(), north: bounds.getNorth()})
      })
      const observer = new ResizeObserver(() => map?.resize())
      observer.observe(container.current!)
      return () => {cancelled = true; clearTimeout(timeout); observer.disconnect(); markers.current.forEach(m => m.remove()); map?.remove(); instance.current = null}
    } catch {
      clearTimeout(timeout); setUnavailable(true)
      return () => {cancelled = true; map?.remove()}
    }
    // The map is created once. Subsequent data updates use the existing instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const map = instance.current
    if (!map || !loaded) return
    ;(map.getSource('chosen-area') as GeoJSONSource | undefined)?.setData(areaShape(run.area))
    map.fitBounds([[run.area.west, run.area.south], [run.area.east, run.area.north]], {padding: 40, duration: 600})
  }, [areaKey, loaded])

  useEffect(() => {
    const map = instance.current
    if (!map) return
    markers.current.forEach(m => m.remove()); markers.current = []
    const selectedCandidate = run.candidates.find(c => c.id === chosenId)
    for (const [index, landmark] of (run.graph?.landmarks ?? []).entries()) {
      const candidateId = landmark.id === selectedId ? chosenId : candidateFor(run, landmark.id)
      const candidate = run.candidates.find(c => c.id === candidateId)
      if (!candidate) continue
      const selected = selectedId === landmark.id
      const button = document.createElement('button')
      button.type = 'button'
      button.className = `map-marker ${selected ? 'selected' : ''} ${run.confirmations[landmark.id]?.candidate_id === candidateId ? 'confirmed' : 'proposed'}`
      button.setAttribute('aria-label', `Select map landmark ${index + 1}: ${landmark.label}`)
      const number = document.createElement('span'); number.className = 'marker-number'; number.textContent = String(index + 1); button.append(number)
      if (selected) {const label = document.createElement('span'); label.className = 'marker-label'; label.textContent = landmark.label; button.append(label)}
      button.addEventListener('click', () => onSelect(landmark.id))
      markers.current.push(new maplibregl.Marker({element: button, anchor: 'center'}).setLngLat([candidate.longitude, candidate.latitude]).addTo(map))
    }
    for (const candidate of run.shortlist[selectedId] ?? []) {
      if (candidate.id === selectedCandidate?.id) continue
      const button = document.createElement('button'); button.type = 'button'; button.className = 'candidate-marker'
      button.setAttribute('aria-label', `Choose alternative: ${candidate.label}`); button.title = candidate.label
      button.textContent = '·'; button.addEventListener('click', () => onChoose(candidate.id))
      markers.current.push(new maplibregl.Marker({element: button}).setLngLat([candidate.longitude, candidate.latitude]).addTo(map))
    }
  }, [run, selectedId, chosenId, loaded, onSelect, onChoose])

  const refit = () => instance.current?.fitBounds([[run.area.west, run.area.south], [run.area.east, run.area.north]], {padding: 40, duration: 600})
  return <section className="panel map-panel" aria-label="Geographic map">
    <div className="panel-heading"><span className="eyebrow"><span className="step-number">02</span> GEOGRAPHIC MAP</span><span className="map-live"><span/>OpenStreetMap</span></div>
    <div className="map-content">
      <div ref={container} className={`map-canvas ${unavailable ? 'map-hidden' : ''}`} />
      {unavailable && <div className="coordinate-fallback">
        <svg viewBox="0 0 500 600" role="img" aria-label="Geographic coordinate plot; street map tiles are unavailable">
          {run.graph?.landmarks.map((p, index) => {
            const c = run.candidates.find(c => c.id === (p.id === selectedId ? chosenId : candidateFor(run, p.id)))
            if (!c) return null
            const x = 40 + (c.longitude - run.area.west) / (run.area.east - run.area.west) * 420
            const y = 560 - (c.latitude - run.area.south) / (run.area.north - run.area.south) * 520
            return <g key={p.id}><circle cx={x} cy={y} r={17} fill={p.id === selectedId ? '#c1f684' : '#222c22'} stroke="#c1f684"/><text x={x} y={y + 5} textAnchor="middle" fill={p.id === selectedId ? '#152014' : '#d6e9cc'} fontSize="13">{index + 1}</text></g>
          })}
        </svg>
        <div className="fallback-note"><WifiOff size={17}/><div>Street map unavailable<span>Locations shown on a coordinate grid. The review list still works.</span></div></div>
      </div>}
      <div className="map-location"><MapPin size={14}/><span>NEGOMBO, SRI LANKA</span></div>
      <button className="fit-map icon-button" aria-label="Fit map to selected area" title="Fit to area" onClick={refit} disabled={unavailable}><Expand size={17}/></button>
      <div className="north-indicator" aria-label="North points up"><Navigation2 size={16}/><span>N</span></div>
      {tilesError && !unavailable && <div className="map-warning">Some map tiles could not load. Locations remain reviewable.</div>}
      <div className="map-legend"><span><i className="legend-dot confirmed"/>Confirmed</span><span><i className="legend-dot proposed"/>Proposed</span><span><i className="legend-dot alternative"/>Alternative</span></div>
      {unavailable && <a className="fallback-attribution" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>}
    </div>
    <div className="map-bottom"><span><span className="subtle-dot"/>Your selected area</span><span className="mono">{((run.area.south + run.area.north) / 2).toFixed(4)}° N · {((run.area.west + run.area.east) / 2).toFixed(4)}° E</span></div>
  </section>
}
