import {useCallback, useEffect, useRef, useState} from 'react'
import {ApiError, api, authenticatedFile, download} from './api'
import {createSample, sampleExport} from './sample'
import {ACTIVE} from './types'
import type {Area, Graph, Health, Run, Session} from './types'

const SESSION_KEY = 'drawn-to-scale-session'
export function useWorkspace() {
  const [run, setRun] = useState<Run>(createSample)
  const [session, setSession] = useState<Session | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [imageUrl, setImageUrl] = useState('/sample-sketch.svg')
  const [fileName, setFileName] = useState('Negombo, from memory.')
  const sample = run.id === 'sample-negombo'
  const version = useRef(0)

  const checkHealth = useCallback(() => {
    api<Health>('/api/health').then(setHealth).catch(() => setHealth(null))
  }, [])
  useEffect(() => {checkHealth()}, [checkHealth])
  useEffect(() => {
    const stored = sessionStorage.getItem(SESSION_KEY)
    if (!stored) return
    let cancelled = false
    try {
      const saved = JSON.parse(stored) as Session & {fileName?: string}
      if (!saved.id || !saved.access_token) throw new Error('Invalid session')
      api<Run>(`/api/runs/${saved.id}`, {}, saved).then(r => {
        if (!cancelled) {setSession(saved); setRun(r); setFileName(saved.fileName || 'Your sketch map')}
      }).catch(() => {if (!cancelled) {sessionStorage.removeItem(SESSION_KEY); setError('Your previous run could not be reopened. The sample is shown instead.')}})
    } catch {sessionStorage.removeItem(SESSION_KEY)}
    return () => {cancelled = true}
  }, [])

  useEffect(() => {
    if (!session) {setImageUrl('/sample-sketch.svg'); return}
    let cancelled = false, url = ''
    authenticatedFile(`/api/runs/${session.id}/image`, session).then(blob => {
      if (cancelled) return
      url = URL.createObjectURL(blob); setImageUrl(url)
    }).catch(e => {if (!cancelled) setError(e.message)})
    return () => {cancelled = true; if (url) URL.revokeObjectURL(url)}
  }, [session])

  const active = ACTIVE.has(run.status)
  useEffect(() => {
    if (!session || !active) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const fresh = await api<Run>(`/api/runs/${session.id}`, {}, session)
        if (cancelled) return
        setRun(fresh)
        if (ACTIVE.has(fresh.status)) timer = setTimeout(poll, 1200)
      } catch (e) {
        if (!cancelled) {setError((e as Error).message); timer = setTimeout(poll, 5000)}
      }
    }
    timer = setTimeout(poll, 500)
    return () => {cancelled = true; clearTimeout(timer)}
  }, [session, active])

  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('')
    try {await action(); return true} catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
      if (e instanceof ApiError && e.status === 409 && session) {
        try {setRun(await api<Run>(`/api/runs/${session.id}`, {}, session))} catch { /* Keep the original error. */ }
      }
      return false
    } finally {setBusy(false)}
  }

  const upload = (image: File, area: Area, context: string) => perform(async () => {
    const body = new FormData()
    body.append('image', image); body.append('area_json', JSON.stringify(area)); body.append('context', context)
    const created = await api<Session>('/api/runs', {method: 'POST', body})
    const fresh = await api<Run>(`/api/runs/${created.id}`, {}, created)
    version.current++
    setSession(created); setRun(fresh); setFileName(image.name.replace(/\.[^.]+$/, ''))
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({...created, fileName: image.name.replace(/\.[^.]+$/, '')}))
  })

  const confirm = (id: string, candidateId: string | null) => perform(async () => {
    const decision = {sketch_id: id, candidate_id: candidateId, note: candidateId ? 'Confirmed in the review workspace' : 'Left unmatched by the author'}
    if (sample) {
      setRun(previous => {
        const confirmations = {...previous.confirmations, [id]: {...decision, actor: 'user'}}
        return {...previous, revision: previous.revision + 1, confirmations,
          status: Object.keys(confirmations).length === previous.graph!.landmarks.length ? 'ready' : 'needs_confirmation'}
      })
    } else if (session) {
      setRun(await api<Run>(`/api/runs/${session.id}/confirmations`, {method: 'PATCH', body: JSON.stringify({revision: run.revision, decisions: [decision]})}, session))
    }
    setNotice(candidateId ? 'Location confirmed. Your map is one step closer.' : 'Landmark left unmatched. It stays in your review report.')
  })

  const editGraph = (graph: Graph) => perform(async () => {
    if (sample) {
      setRun(previous => ({...previous, graph, revision: previous.revision + 1, confirmations: {},
        alignment: {status: 'sample', independent_error_m: null}, status: 'needs_confirmation',
        proposals: graph.landmarks.map(p => ({sketch_id: p.id, candidate_id: null, evidence: 'Sketch edited. Review this location again.'}))}))
    } else if (session) {
      setRun(await api<Run>(`/api/runs/${session.id}/graph`, {method: 'PATCH', body: JSON.stringify({revision: run.revision, graph, note: 'Corrected in the sketch workspace'})}, session))
    }
    setNotice('Sketch updated. Previous confirmations were cleared so you can review the new interpretation.')
  })

  const exportFile = (report = false) => perform(async () => {
    if (sample) {
      const content = report
        ? ['DRAWN TO SCALE — ILLUSTRATIVE SAMPLE', 'No model run. No accuracy measured.', '',
          ...run.graph!.landmarks.map(p => `${p.label}: ${run.confirmations[p.id]?.candidate_id ? 'confirmed in sample' : run.confirmations[p.id] ? 'unmatched' : 'needs review'}`),
          '', '© OpenStreetMap contributors — https://www.openstreetmap.org/copyright'].join('\n')
        : JSON.stringify(sampleExport(run), null, 2)
      download(new Blob([content], {type: report ? 'text/plain' : 'application/geo+json'}), report ? 'negombo-sample-review.txt' : 'negombo-sample.geojson')
    } else if (session) {
      download(await authenticatedFile(`/api/runs/${session.id}/${report ? 'report' : 'export'}`, session), `drawn-to-scale-${session.id}.${report ? 'txt' : 'geojson'}`)
    }
    setNotice(report ? 'Review summary downloaded.' : 'GeoJSON downloaded. Unresolved landmarks are included in its metadata.')
  })

  const retry = () => perform(async () => {
    if (session) {
      await api(`/api/runs/${session.id}/retry`, {method: 'POST'}, session)
      setRun(await api<Run>(`/api/runs/${session.id}`, {}, session))
    }
  })
  const reset = () => {
    version.current++; sessionStorage.removeItem(SESSION_KEY); setSession(null)
    setRun(createSample()); setFileName('Negombo, from memory.'); setError(''); setNotice('')
  }
  const deleteRun = () => perform(async () => {
    if (session) await api(`/api/runs/${session.id}`, {method: 'DELETE'}, session)
    reset(); setNotice('Run and uploaded image deleted. You are back in the sample workspace.')
  })
  return {run, sample, health, busy, error, notice, imageUrl, fileName, active,
    upload, confirm, editGraph, exportFile, retry, reset, deleteRun, checkHealth,
    clearError: () => setError(''), clearNotice: () => setNotice('')}
}
