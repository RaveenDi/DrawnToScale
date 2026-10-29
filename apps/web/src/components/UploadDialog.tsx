import {useEffect, useRef, useState} from 'react'
import {ArrowRight, Check, ChevronDown, FileImage, LocateFixed, MapPin, UploadCloud, X} from 'lucide-react'
import Modal from './Modal'
import type {Area, Health} from '../types'
import {NEGOMBO} from '../types'

function areaError(area: Area) {
  if (Object.values(area).some(n => !Number.isFinite(n))) return 'Enter all four area coordinates.'
  if (area.south < -80 || area.north > 80 || area.west < -180 || area.east > 180) return 'Coordinates are outside the supported range.'
  if (area.west >= area.east || area.south >= area.north) return 'West must be before east, and south before north.'
  const height = (area.north - area.south) * 111_200
  const width = (area.east - area.west) * 111_200 * Math.cos((area.north + area.south) / 2 * Math.PI / 180)
  return Math.hypot(width, height) > 20_000 ? 'Choose a smaller neighbourhood, less than 20 km across.' : ''
}

export default function UploadDialog({onClose, onUpload, busy, health, viewport, onSetup, serverError}: {
  onClose: () => void; onUpload: (file: File, area: Area, context: string) => Promise<boolean>
  busy: boolean; health: Health | null; viewport: Area | null; onSetup: () => void; serverError: string
}) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [area, setArea] = useState<Area>({...NEGOMBO})
  const [context, setContext] = useState('')
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!file) {setPreview(''); return}
    const url = URL.createObjectURL(file); setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])
  const choose = (next?: File) => {
    if (!next) return
    setError('')
    if (!['image/png', 'image/jpeg'].includes(next.type)) {setError('Choose a PNG or JPEG image.'); return}
    if (next.size > 8 * 1024 * 1024) {setError('Choose an image smaller than 8 MB.'); return}
    setFile(next)
  }
  const geographicError = areaError(area)
  return <Modal title="Start with a place you know." eyebrow="A NEW MAP" onClose={busy ? () => {} : onClose} wide>
    <p className="modal-intro">A few landmarks, a familiar neighbourhood, your own perspective.</p>
    <div className={`upload-drop ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
      onDragOver={e => {e.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)}
      onDrop={e => {e.preventDefault(); setDragging(false); if (!busy) choose(e.dataTransfer.files[0])}}>
      <input ref={input} type="file" accept="image/png,image/jpeg" className="sr-only" aria-label="Upload sketch image" onChange={e => choose(e.target.files?.[0])} disabled={busy}/>
      {file ? <><img src={preview} alt="Sketch upload preview" onLoad={e => {
        if (e.currentTarget.naturalWidth * e.currentTarget.naturalHeight > 20_000_000) {setFile(null); setError('Choose an image of at most 20 megapixels.')}
      }} onError={() => {setFile(null); setError('This image could not be read. Choose another PNG or JPEG.')}}/>
        <div className="uploaded-file"><FileImage size={17}/><div><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(1)} MB · ready to upload</span></div>
          <button className="icon-button" onClick={() => setFile(null)} aria-label="Remove chosen image" disabled={busy}><X size={17}/></button></div></>
        : <button className="upload-trigger" onClick={() => input.current?.click()} disabled={busy}><span className="upload-icon"><UploadCloud size={26}/></span><strong>Drop your sketch here</strong><span>or <u>browse files</u></span><small>PNG or JPG · up to 8 MB</small></button>}
    </div>
    {(error || serverError) && <p className="form-error" role="alert">{error || serverError}</p>}
    <div className="upload-area"><div><MapPin size={18}/><div><strong>{JSON.stringify(area) === JSON.stringify(NEGOMBO) ? 'Central Negombo, Sri Lanka' : 'Your selected area'}</strong><span>We’ll look for places inside this area.</span></div></div>
      <button className="button subtle small" onClick={() => setExpanded(v => !v)} aria-expanded={expanded}>Adjust <ChevronDown size={13}/></button></div>
    {expanded && <div className="area-editor"><div className="area-fields">{(['west', 'south', 'east', 'north'] as const).map(key => <label className="field" key={key}>{key}<input type="number" step=".001" value={Number.isFinite(area[key]) ? area[key] : ''} onChange={e => setArea({...area, [key]: e.target.value === '' ? NaN : Number(e.target.value)})}/></label>)}</div>
      <div className="area-actions"><button className="inline-link" disabled={!viewport} onClick={() => viewport && setArea({...viewport})}><LocateFixed size={13}/>Use the current map view</button><button className="inline-link" onClick={() => setArea({...NEGOMBO})}>Reset to Negombo</button></div>
      {geographicError && <p className="form-error">{geographicError}</p>}</div>}
    <label className="field context-field">Anything we should know? <span className="muted">optional</span><textarea value={context} onChange={e => setContext(e.target.value)} maxLength={2000} placeholder="For example: the arrow points north; the little square is a shop." rows={2}/></label>
    {!health?.meta_configured && <div className="connection-note"><CircleConnection/><div><strong>{health ? 'Connect Meta to read your sketch' : 'The local service is not connected'}</strong><span>You can keep exploring the sample while you set it up.</span></div><button className="inline-link" onClick={onSetup}>Setup <ArrowRight size={13}/></button></div>}
    <div className="modal-footer"><span className="helper"><Check size={13}/> Only upload a sketch you have permission to use.</span>
      <button className="button primary" disabled={busy || !file || !!geographicError || !health?.meta_configured || !!error} onClick={async () => {if (file && await onUpload(file, area, context)) onClose()}}>{busy ? 'Starting your map…' : 'Read my sketch'}<ArrowRight size={16}/></button></div>
  </Modal>
}
function CircleConnection() {return <span className="connection-ring"/>}
