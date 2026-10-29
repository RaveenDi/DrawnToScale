import {useState} from 'react'
import {ArrowDown, ArrowUpRight, Check, CheckCheck, ChevronRight, CircleHelp, MapPin, Pencil, Search, Undo2, X} from 'lucide-react'
import type {Landmark, Run} from '../types'
import {landmarkState} from '../types'

export default function ReviewPanel({run, selectedId, onSelect, chosenId, onChoose, busy, disabled, onConfirm, onUnmatched, draft, onEdit, onDraft, onCancelEdit, onSaveEdit}: {
  run: Run; selectedId: string; onSelect: (id: string) => void; chosenId: string | null; onChoose: (id: string | null) => void
  busy: boolean; disabled: boolean; onConfirm: () => void; onUnmatched: () => void
  draft: Landmark | null; onEdit: () => void; onDraft: (p: Landmark) => void; onCancelEdit: () => void; onSaveEdit: () => void
}) {
  const [search, setSearch] = useState('')
  const [searchAll, setSearchAll] = useState(false)
  const landmarks = run.graph?.landmarks ?? []
  const selected = landmarks.find(p => p.id === selectedId)
  const confirmed = Object.values(run.confirmations).filter(d => d.candidate_id).length
  const reviewed = Object.keys(run.confirmations).length
  const candidate = run.candidates.find(c => c.id === chosenId)
  const proposal = run.proposals.find(p => p.sketch_id === selectedId)
  const selectedDecision = run.confirmations[selectedId]
  const used = Object.entries(run.confirmations).filter(([id, decision]) => id !== selectedId && decision.candidate_id).map(([, d]) => d.candidate_id)
  const alternatives = (searchAll ? run.candidates : run.shortlist[selectedId] ?? []).filter(c =>
    !search || [c.label, ...c.aliases].some(n => n.toLowerCase().includes(search.toLowerCase())))
  const choices = candidate && !alternatives.find(c => c.id === candidate.id) ? [candidate, ...alternatives] : alternatives

  return <aside className="panel review-panel" aria-label="Review landmarks">
    <div className="review-heading"><div className="eyebrow">THE HUMAN PART</div><div className="review-title"><h2>Make it your map.</h2><span className="count-pill">{landmarks.length}</span></div>
      <p>Check the places. You know them best.</p>
      <div className="review-progress"><span style={{width: `${landmarks.length ? reviewed / landmarks.length * 100 : 0}%`}}/></div>
      <div className="progress-caption"><span>{reviewed} of {landmarks.length} reviewed</span><span>{confirmed} confirmed</span></div>
    </div>
    <div className="landmark-list">
      {landmarks.map((p, index) => {
        const status = landmarkState(run, p.id)
        return <button className={`landmark-item ${p.id === selectedId ? 'active' : ''}`} key={p.id} onClick={() => {onSelect(p.id); setSearch(''); setSearchAll(false)}} disabled={!!draft || busy} aria-pressed={p.id === selectedId}>
          <span className={`landmark-number ${status}`}>{status === 'confirmed' ? <Check size={13}/> : index + 1}</span>
          <span className="landmark-copy"><strong>{p.label || 'Unnamed landmark'}</strong><span className={status === 'confirmed' ? 'text-lime' : ''}>{status === 'needs review' ? run.proposals.find(m => m.sketch_id === p.id)?.candidate_id ? 'Proposed match' : 'Needs your help' : status === 'confirmed' ? 'Confirmed' : 'Left unmatched'}</span></span>
          {p.id === selectedId ? <ChevronRight size={14}/> : status === 'unmatched' ? <CircleHelp size={14}/> : null}
        </button>
      })}
      {!landmarks.length && <div className="quiet-empty"><Search size={23}/><p>{disabled ? 'Your landmarks will appear here.' : 'No landmarks were extracted. Try a clearer sketch.'}</p></div>}
    </div>
    {selected && <div className="review-detail" key={selected.id}>
      {draft ? <>
        <div className="detail-heading"><span className="eyebrow">EDIT LANDMARK</span><button className="icon-button small" onClick={onCancelEdit} aria-label="Cancel landmark edit"><X size={15}/></button></div>
        <label className="field">Label<input value={draft.label} maxLength={120} onChange={e => onDraft({...draft, label: e.target.value})}/></label>
        <label className="field">Type<input value={draft.type} maxLength={60} onChange={e => onDraft({...draft, type: e.target.value})}/></label>
        <div className="field-row"><label className="field">Across (%)<input type="number" min="0" max="100" step=".1" value={Number((draft.x * 100).toFixed(1))} onChange={e => onDraft({...draft, x: Math.min(1, Math.max(0, Number(e.target.value) / 100))})}/></label>
          <label className="field">Down (%)<input type="number" min="0" max="100" step=".1" value={Number((draft.y * 100).toFixed(1))} onChange={e => onDraft({...draft, y: Math.min(1, Math.max(0, Number(e.target.value) / 100))})}/></label></div>
        <p className="helper">You can also click on the sketch to move the point. Saving clears previous confirmations for a fresh review.</p>
        <button className="button primary full" onClick={onSaveEdit} disabled={busy || !draft.label.trim()}><Check size={15}/>{busy ? 'Saving…' : 'Save correction'}</button>
        <button className="button quiet full" onClick={onCancelEdit} disabled={busy}>Cancel</button>
      </> : <>
        <div className="detail-heading"><span className="eyebrow">{selectedDecision?.candidate_id ? 'CONFIRMED LOCATION' : 'SUGGESTED LOCATION'}</span>
          <button className="icon-button small" title="Edit sketch label or position" aria-label="Edit selected landmark" onClick={onEdit} disabled={disabled || busy}><Pencil size={14}/></button></div>
        <div className="candidate-card"><div className="candidate-symbol"><MapPin size={18}/></div><div><strong>{candidate?.label ?? 'A little local knowledge?'}</strong><span>{candidate ? candidate.type.replaceAll('_', ' ') : 'Choose a place, or leave it unmatched.'}</span></div></div>
        {candidate && <div className="candidate-coordinates mono">{candidate.latitude.toFixed(5)}° N, {candidate.longitude.toFixed(5)}° E
          <a href={candidate.source_url} target="_blank" rel="noreferrer" title="Inspect geographic source" aria-label="Open selected location source"><ArrowUpRight size={15}/></a></div>}
        <p className="match-evidence">{proposal?.evidence ?? selected.evidence}</p>
        {candidate?.position_kind === 'bounding_box_center' && <p className="center-note">Map position is the feature's center, not its entrance.</p>}
        <div className="candidate-selector">
          <label className="field">{searchAll ? 'Search the area' : 'Location on the map'}
            {searchAll && <div className="search-field"><Search size={14}/><input autoFocus placeholder="Search place names…" value={search} onChange={e => setSearch(e.target.value)}/></div>}
            <select aria-label="Choose geographic candidate" value={chosenId ?? ''} onChange={e => onChoose(e.target.value || null)} disabled={disabled || busy}>
              <option value="">Choose a location…</option>
              {choices.map(c => <option key={c.id} value={c.id} disabled={used.includes(c.id)}>{c.label}{used.includes(c.id) ? ' · already used' : ''}</option>)}
            </select>
          </label>
          <button className="inline-link" onClick={() => {setSearchAll(s => !s); setSearch('')}} disabled={disabled}>{searchAll ? 'Show suggested places' : `Search all ${run.candidates.length} places`} <Search size={12}/></button>
        </div>
        <button className="button primary full confirm-button" onClick={onConfirm} disabled={disabled || busy || !chosenId || used.includes(chosenId) || selectedDecision?.candidate_id === chosenId}>
          {selectedDecision?.candidate_id === chosenId && chosenId ? <CheckCheck size={16}/> : <Check size={16}/>}
          {busy ? 'Saving…' : selectedDecision?.candidate_id === chosenId && chosenId ? 'Location confirmed' : 'Confirm location'}
        </button>
        <button className="button quiet full" onClick={onUnmatched} disabled={disabled || busy || selectedDecision?.candidate_id === null}><Undo2 size={14}/>{selectedDecision?.candidate_id === null ? 'Left unmatched' : 'Leave unmatched'}</button>
      </>}
    </div>}
    {reviewed === landmarks.length && landmarks.length > 0 && <div className="review-done"><CheckCheck size={17}/><span>Review complete. Your export is ready.</span><ArrowDown size={14}/></div>}
  </aside>
}
