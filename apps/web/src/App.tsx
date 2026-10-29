import {lazy, Suspense, useCallback, useEffect, useState} from 'react'
import {ArrowDownToLine, ArrowRight, ArrowUpRight, Check, CheckCheck, CircleHelp, Compass, FileText, Layers2, LoaderCircle, Map, MapPin, Plus, RotateCcw, Settings2, Sparkles, Trash2, Upload, X} from 'lucide-react'
import SketchPanel from './components/SketchPanel'
import ReviewPanel from './components/ReviewPanel'
import UploadDialog from './components/UploadDialog'
import Modal from './components/Modal'
import {useWorkspace} from './useWorkspace'
import {candidateFor} from './types'
import type {Area, Landmark} from './types'

const MapPanel = lazy(() => import('./components/MapPanel'))
type Dialog = 'upload' | 'export' | 'help' | 'setup' | 'delete' | null

export default function App() {
  const workspace = useWorkspace()
  const {run, sample, health, busy, error, notice, active} = workspace
  const [selectedId, setSelectedId] = useState('p1')
  const [chosenId, setChosenId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Landmark | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [viewport, setViewport] = useState<Area | null>(null)
  const landmarks = run.graph?.landmarks ?? []
  const confirmed = Object.values(run.confirmations).filter(d => d.candidate_id).length
  const unmatched = Object.values(run.confirmations).filter(d => !d.candidate_id).length
  const remaining = landmarks.length - Object.keys(run.confirmations).length
  const editable = ['ready', 'needs_confirmation'].includes(run.status)

  useEffect(() => {
    if (landmarks.length && !landmarks.some(p => p.id === selectedId)) setSelectedId(landmarks[0].id)
    setDraft(null)
  }, [run.id, run.graph])
  useEffect(() => {setChosenId(candidateFor(run, selectedId))}, [selectedId, run.id, run.revision])
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(workspace.clearNotice, 6500)
    return () => clearTimeout(timer)
  }, [notice])

  const selectLandmark = useCallback((id: string) => {setSelectedId(id); setDraft(null)}, [])
  const chooseCandidate = useCallback((id: string | null) => setChosenId(id), [])
  const finishReview = async (candidate: string | null) => {
    if (await workspace.confirm(selectedId, candidate)) {
      const next = landmarks.find(p => p.id !== selectedId && !run.confirmations[p.id])
      if (next) setSelectedId(next.id)
    }
  }
  const selectedIndex = landmarks.findIndex(p => p.id === selectedId)
  const openNew = () => {workspace.clearError(); setDialog('upload')}

  return <div className="app-shell">
    <aside className="sidebar" aria-label="Workspace navigation">
      <a href="/" className="brand-symbol" aria-label="Drawn to Scale home"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M6 22V8h14M12 26h14V12M5 27L27 5"/></svg></a>
      <div className="rail-nav"><button className="rail-button active" title="Map workspace" aria-label="Map workspace" onClick={() => document.querySelector('.workspace-title')?.scrollIntoView({behavior: 'smooth'})}><Layers2 size={21}/></button>
        <button className="rail-button" title="New sketch" aria-label="New sketch" onClick={openNew} disabled={busy || active}><Upload size={20}/></button>
        <button className="rail-button" title="Export map" aria-label="Export map" onClick={() => setDialog('export')} disabled={!editable || busy}><ArrowDownToLine size={20}/></button></div>
      <div className="rail-bottom"><button className="rail-button" aria-label="How it works" title="How it works" onClick={() => setDialog('help')}><CircleHelp size={20}/></button>
        <button className="rail-button" aria-label="Connection settings" title="Connection settings" onClick={() => setDialog('setup')}><Settings2 size={20}/></button><span className="rail-avatar" title="Your local workspace">You</span></div>
    </aside>

    <div className="app-main">
      <header className="topbar"><div className="wordmark">drawn<span className="wordmark-to"> to </span>scale<span className="brand-period">.</span></div><span className="header-separator"/><span className="header-location">Workspace</span>
        <div className="topbar-right"><button className={`connection-status ${health?.meta_configured ? 'connected' : ''}`} onClick={() => setDialog('setup')}><i/>{health?.meta_configured ? 'Connected' : 'Local workspace'}<ChevronSmall/></button><span className="version">EARLY ACCESS</span></div></header>
      <main>
        <div className="workspace-title"><div><div className="eyebrow title-eyebrow"><span className="tiny-line"/>{sample ? 'SAMPLE WORKSPACE' : 'YOUR WORKSPACE'}<span className="title-slash">/</span>NEGOMBO</div><h1>{workspace.fileName}</h1><p>A place you know. A map you can share.</p></div>
          <div className="workspace-actions"><button className="button subtle" onClick={openNew} disabled={busy || active}><Plus size={16}/>New sketch</button><button className="button primary" onClick={() => setDialog('export')} disabled={!editable || busy}><ArrowDownToLine size={16}/>Export map</button></div>
        </div>
        <div className="workspace-meta"><div className="workflow-steps"><span className="step-complete"><Check size={12}/><span>Sketch</span></span><div/><span className="step-current"><span className="step-circle">2</span><span>Review & refine</span></span><div/><span className={remaining === 0 && landmarks.length ? 'step-complete' : ''}><span className="step-circle">3</span><span>Share your map</span></span></div>
          {sample ? <span className="sample-tag"><Sparkles size={12}/>Illustrative sample · no AI run</span> : <span className="sample-tag"><MapPin size={12}/>{landmarks.length} landmarks found</span>}</div>
        {error && <div className="message error-message" role="alert"><CircleHelp size={17}/><span>{error}</span><button className="icon-button small" onClick={workspace.clearError} aria-label="Dismiss error"><X size={15}/></button></div>}
        {active && <div className="message processing-message" role="status"><LoaderCircle className="spin" size={18}/><div><strong>{({created: 'Getting your sketch ready', extracting: 'Reading your sketch', retrieving: 'Finding places in your area', matching: 'Comparing landmarks'})[run.status] || 'Processing your sketch'}</strong><span>Your landmarks will appear as soon as they are ready.</span></div></div>}
        {['failed', 'interrupted'].includes(run.status) && <div className="message error-message"><CircleHelp size={18}/><div><strong>Let’s pick up where we left off.</strong><span>{run.error}</span></div><button className="button subtle small" onClick={workspace.retry} disabled={busy || run.attempts >= 3}><RotateCcw size={14}/>Retry</button></div>}

        <div className="workspace-grid">
          <SketchPanel run={run} imageUrl={workspace.imageUrl} selectedId={selectedId} onSelect={selectLandmark} draft={draft} sample={sample} onPosition={(x, y) => draft && setDraft({...draft, x, y})}/>
          <Suspense fallback={<section className="panel map-loading"><LoaderCircle className="spin" size={24}/><span>Opening your map…</span></section>}><MapPanel run={run} selectedId={selectedId} chosenId={chosenId} onSelect={selectLandmark} onChoose={chooseCandidate} onViewport={setViewport}/></Suspense>
          <ReviewPanel run={run} selectedId={selectedId} chosenId={chosenId} onSelect={selectLandmark} onChoose={chooseCandidate} busy={busy} disabled={!editable} onConfirm={() => finishReview(chosenId)} onUnmatched={() => finishReview(null)} draft={draft}
            onEdit={() => {if (selectedIndex >= 0) setDraft({...landmarks[selectedIndex]})}} onDraft={setDraft} onCancelEdit={() => setDraft(null)} onSaveEdit={async () => {
              if (draft && run.graph && await workspace.editGraph({...run.graph, landmarks: run.graph.landmarks.map(p => p.id === draft.id ? draft : p)})) setDraft(null)
            }}/>
        </div>

        <div className="workspace-footer"><div><span className="footer-icon"><Compass size={17}/></span><p><strong>Your knowledge. The final say.</strong><span>Only places you confirm become points on your exported map.</span></p></div><button className="inline-link" onClick={() => setDialog('help')}>How it works<ArrowUpRight size={14}/></button></div>
        <div className="bottom-status"><span><span className="subtle-dot"/>{sample ? 'Sample changes stay in this tab' : 'Changes saved to this local workspace'}</span><span>{run.alignment?.status === 'fitted' ? 'Alignment fitted · independent accuracy unmeasured' : 'Every map starts with a little local knowledge.'}</span>
          {!sample && <button className="inline-link danger-link" onClick={() => setDialog('delete')} disabled={active || busy}>Delete this run</button>}</div>
      </main>
    </div>

    {notice && <div className="toast" role="status"><CheckCheck size={17}/><span>{notice}</span><button className="icon-button small" aria-label="Dismiss notification" onClick={workspace.clearNotice}><X size={14}/></button></div>}
    {dialog === 'upload' && <UploadDialog onClose={() => setDialog(null)} onUpload={workspace.upload} busy={busy} health={health} viewport={viewport} onSetup={() => setDialog('setup')} serverError={error}/>}
    {dialog === 'export' && <Modal title="A map, ready to leave the page." eyebrow="EXPORT YOUR WORK" onClose={() => setDialog(null)}>
      <p className="modal-intro">Keep the places you’ve confirmed, with their sources and your review notes.</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="export-counts"><div><strong>{confirmed}</strong><span>confirmed</span></div><div><strong>{remaining}</strong><span>need review</span></div><div><strong>{unmatched}</strong><span>unmatched</span></div></div>
      {sample && <div className="sample-disclaimer"><Sparkles size={16}/><span>This export is labeled as a sample. No AI was run, and no location accuracy has been measured.</span></div>}
      {remaining > 0 && <p className="helper">You can export now. Unreviewed and unmatched places stay in the report; they are not exported as map points.</p>}
      {run.alignment?.status === 'needs_review' && <p className="form-error">The anchor alignment needs review. Confirmed source points can still be exported, but the sketch alignment is unresolved.</p>}
      <button className="export-option" onClick={() => workspace.exportFile(false)} disabled={!confirmed || busy}><span className="export-icon"><Map size={24}/></span><span><strong>GeoJSON map</strong><small>Confirmed points, source links and unresolved items</small></span><ArrowDownToLine size={19}/></button>
      {!confirmed && <p className="helper">Confirm at least one place to download a map.</p>}
      <button className="export-option" onClick={() => workspace.exportFile(true)} disabled={busy}><span className="export-icon"><FileText size={24}/></span><span><strong>Review summary</strong><small>A printable text record of your decisions</small></span><ArrowDownToLine size={19}/></button>
      <p className="license-note">Map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>. Independent accuracy has not been measured.</p>
    </Modal>}
    {dialog === 'help' && <Modal title="From a sketch to a shared place." eyebrow="HOW IT WORKS" onClose={() => setDialog(null)}>
      <div className="help-steps"><div><span>01</span><section><h3>Start with your sketch</h3><p>Upload a drawing of a small area you know. Include a few named landmarks and choose the part of the world it belongs to.</p></section></div>
        <div><span>02</span><section><h3>Bring your local knowledge</h3><p>Select a numbered landmark on the sketch or map. Check the source, choose the correct place and confirm it. You can correct the sketch or leave a place unmatched.</p></section></div>
        <div><span>03</span><section><h3>Take your map with you</h3><p>Export confirmed points as GeoJSON, together with their sources. Download the review summary to keep track of what remains unresolved.</p></section></div></div>
      <div className="sample-disclaimer"><CircleHelp size={17}/><span>This is an early prototype. Real sketch-reading performance is still untested. The sample shows the workflow, not a measured result.</span></div>
      <div className="modal-footer"><button className="button subtle" onClick={() => setDialog(null)}>Back to my map</button><button className="button primary" onClick={openNew} disabled={active || busy}>Try my sketch<ArrowRight size={16}/></button></div>
    </Modal>}
    {dialog === 'setup' && <Modal title="Your local workspace." eyebrow="CONNECTION" onClose={() => setDialog(null)}>
      <div className="setup-status"><span className={`status-orb ${health ? 'ready' : ''}`}/><div><strong>{health ? 'Local service is running' : 'Local service is unavailable'}</strong><span>{health?.meta_configured ? 'Meta is configured. You can upload a sketch.' : 'The sample works without a Meta connection.'}</span></div></div>
      <div className="setup-instructions"><h3>{health?.meta_configured ? 'Ready for your first real sketch' : 'Connect Meta to read your own sketch'}</h3>
        <p>{health?.meta_configured ? 'Your key stays on the local service. Uploaded sketches are sent to Meta when you choose “Read my sketch”.' : <>Add your key as <code>MODEL_API_KEY</code> in <code>project/.env</code>, then restart the local service. Your key stays on the server.</>}</p>
        {!health && <p>Start the app using the project README, then check the connection again.</p>}
        <p className="helper">Map tiles are provided by OpenFreeMap. Place data comes from OpenStreetMap. Reading a real sketch uses your configured model account.</p></div>
      <div className="modal-footer"><button className="button subtle" onClick={workspace.checkHealth}><RotateCcw size={15}/>Check connection</button><button className="button primary" onClick={openNew}>Choose my sketch<ArrowRight size={16}/></button></div>
    </Modal>}
    {dialog === 'delete' && <Modal title="Delete this sketch and run?" eyebrow="REMOVE LOCAL WORK" onClose={() => setDialog(null)}><p className="modal-intro">This removes the uploaded image, saved interpretation and corrections from this workspace. Download your map first if you want to keep it.</p><div className="modal-footer"><button className="button subtle" onClick={() => setDialog(null)}>Keep working</button><button className="button danger" disabled={busy} onClick={async () => {if (await workspace.deleteRun()) setDialog(null)}}><Trash2 size={15}/>Delete run</button></div></Modal>}
  </div>
}
function ChevronSmall() {return <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 4L5 7L8 4" stroke="currentColor" fill="none" strokeWidth="1.3"/></svg>}
