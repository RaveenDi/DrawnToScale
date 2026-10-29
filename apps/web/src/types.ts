export type Area = {west: number; south: number; east: number; north: number}
export type Landmark = {id: string; label: string; type: string; x: number; y: number; evidence: string}
export type Graph = {
  north_direction: 'up' | 'right' | 'down' | 'left' | 'unknown'
  landmarks: Landmark[]
  relations: {from_id: string; to_id: string; kind: string; evidence: string}[]
  unresolved: string[]
}
export type Candidate = {
  id: string; label: string; aliases: string[]; type: string; longitude: number; latitude: number
  source_url: string; position_kind: 'node' | 'bounding_box_center' | 'synthetic'
}
export type Decision = {sketch_id: string; candidate_id: string | null; note: string; actor?: string}
export type Alignment = {
  status: string; accepted?: number; required?: number; reason?: string; independent_error_m?: number | null
  anchors?: {sketch_id: string; inlier: boolean; fit_residual_m: number}[]; note?: string
}
export type Run = {
  id: string; revision: number; status: string; created_at: string; area: Area
  image: {width: number; height: number; sha256: string}; graph: Graph | null; original_graph: Graph | null
  candidates: Candidate[]; shortlist: Record<string, Candidate[]>
  proposals: {sketch_id: string; candidate_id: string | null; evidence: string}[]
  confirmations: Record<string, Decision>; alignment: Alignment | null
  candidate_manifest: {attribution?: string; license_url?: string; retrieved_at?: string; candidate_count?: number; note?: string} | null
  error: string | null; model_calls: number; attempts: number; trace?: TraceStep[]
}
export type TraceStep = {agent: string; role: string; model: string; input_summary: string; output_summary: string; duration_ms: number; status: string}
export type Session = {id: string; access_token: string}
export type Health = {status: string; meta_configured: boolean; provider?: string}
export const ACTIVE = new Set(['created', 'extracting', 'retrieving', 'matching'])
export const NEGOMBO: Area = {west: 79.823, south: 7.199, east: 79.848, north: 7.222}
export function landmarkState(run: Run, id: string) {
  const decision = run.confirmations[id]
  if (decision) return decision.candidate_id ? 'confirmed' : 'unmatched'
  return 'needs review'
}
export function candidateFor(run: Run, id: string) {
  const decision = run.confirmations[id]
  return decision ? decision.candidate_id : run.proposals.find(p => p.sketch_id === id)?.candidate_id ?? null
}
