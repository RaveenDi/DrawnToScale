import {useRef, useState} from 'react'
import {Crosshair, ImageIcon, Minus, Plus, RotateCcw} from 'lucide-react'
import type {Graph, Landmark, Run} from '../types'
import {landmarkState} from '../types'

export default function SketchPanel({run, imageUrl, selectedId, onSelect, draft, onPosition, sample}: {
  run: Run; imageUrl: string; selectedId: string; onSelect: (id: string) => void
  draft: Landmark | null; onPosition: (x: number, y: number) => void; sample: boolean
}) {
  const [zoom, setZoom] = useState(1)
  const page = useRef<HTMLDivElement>(null)
  const graph: Graph | null = run.graph
  return <section className="panel sketch-panel" aria-label="Source sketch">
    <div className="panel-heading"><span className="eyebrow"><span className="step-number">01</span> SOURCE SKETCH</span><ImageIcon size={15}/></div>
    <div className="sketch-tools"><span className="file-label">{sample ? 'negombo-memory-map.svg' : 'Your original sketch'}</span><span className="small-pill">{sample ? 'Sample' : 'Uploaded'}</span></div>
    <div className={`sketch-surface ${draft ? 'is-editing' : ''}`}>
      <div ref={page} className="sketch-paper" style={{aspectRatio: `${run.image.width} / ${run.image.height}`, transform: `rotate(${draft ? 0 : -1.4}deg) scale(${zoom})`}}
        onClick={e => {
          if (!draft || !page.current) return
          const rect = page.current.getBoundingClientRect()
          onPosition(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)))
        }}>
        <img src={imageUrl} alt={sample ? 'Illustrative hand-drawn map of Negombo with a church, clock tower, temple, fish market and coconut stall' : 'Your uploaded sketch, in the orientation used for extraction'} draggable={false}/>
        {graph?.landmarks.map((point, index) => {
          const p = draft?.id === point.id ? draft : point
          return <button key={point.id} className={`sketch-pin ${selectedId === point.id ? 'selected' : ''} ${landmarkState(run, point.id)}`}
            style={{left: `${p.x * 100}%`, top: `${p.y * 100}%`}} title={point.label}
            onClick={e => {e.stopPropagation(); if (!draft) onSelect(point.id)}} aria-label={`Select sketch landmark ${index + 1}: ${point.label}`}>
            {index + 1}
          </button>
        })}
      </div>
    </div>
    <div className="sketch-bottom"><span><Crosshair size={14}/>{draft ? 'Click the sketch to move this point' : 'Select a numbered landmark'}</span>
      <div className="zoom-controls"><button aria-label="Zoom sketch out" disabled={zoom <= .8} onClick={() => setZoom(z => Math.max(.8, z - .1))}><Minus size={14}/></button>
        <button aria-label="Reset sketch zoom" onClick={() => setZoom(1)} title="Reset zoom">{Math.round(zoom * 100)}%</button>
        <button aria-label="Zoom sketch in" disabled={zoom >= 1.8} onClick={() => setZoom(z => Math.min(1.8, z + .1))}><Plus size={14}/></button></div>
    </div>
    {!graph && <div className="panel-busy"><RotateCcw className="spin" size={19}/><span>Reading the landmarks in your sketch…</span></div>}
  </section>
}
