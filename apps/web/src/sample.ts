import data from './data/sample-candidates.json'
import type {Candidate, Graph, Run} from './types'
import {NEGOMBO} from './types'

const candidates = data.candidates as Candidate[]
export function createSample(): Run {
  const graph: Graph = {
    north_direction: 'up',
    landmarks: [
      {id: 'p1', label: "St. Mary's Church", type: 'place_of_worship', x: .71, y: .42, evidence: 'Illustrative sample: church drawn beside the main road.'},
      {id: 'p2', label: 'Clock Tower', type: 'landmark', x: .32, y: .49, evidence: 'Illustrative sample: clock at the junction.'},
      {id: 'p3', label: 'Sri Sitthi Vinayagar Temple', type: 'place_of_worship', x: .73, y: .17, evidence: 'Illustrative sample: temple in the upper-right corner.'},
      {id: 'p4', label: 'Fish market', type: 'seafood', x: .24, y: .78, evidence: 'Illustrative sample: market near the water. More than one geographic candidate.'},
      {id: 'p5', label: 'Coconut stall', type: 'shop', x: .65, y: .73, evidence: 'Illustrative sample: a small stall with no known geographic match.'},
    ],
    relations: [], unresolved: ['The coconut stall has no supplied geographic candidate.'],
  }
  return {
    id: 'sample-negombo', revision: 0, status: 'needs_confirmation', created_at: '2026-10-29', area: {...NEGOMBO},
    image: {width: 700, height: 850, sha256: 'illustrative-sample-not-a-model-input'},
    graph, original_graph: structuredClone(graph), candidates,
    shortlist: {p1: [candidates[0], candidates[5]], p2: [candidates[1]], p3: [candidates[2]], p4: [candidates[3], candidates[4]], p5: []},
    proposals: graph.landmarks.map((p, i) => ({sketch_id: p.id, candidate_id: i < 3 ? candidates[i].id : null,
      evidence: i < 3 ? 'Example correspondence supplied for this interface preview. Please inspect the source.'
        : i === 3 ? 'Two places share this name. Choose the location you intended.' : 'No candidate supplied. You can leave this landmark unmatched.'})),
    confirmations: {}, alignment: {status: 'sample', independent_error_m: null},
    candidate_manifest: {...data.manifest, candidate_count: candidates.length, note: 'Six real OSM records, paired with an illustrative sketch. Matches are authored examples, not model results.'},
    model_calls: 0, attempts: 0, error: null,
  }
}
export function sampleExport(run: Run) {
  return {
    type: 'FeatureCollection',
    features: run.graph!.landmarks.flatMap(p => {
      const id = run.confirmations[p.id]?.candidate_id
      const c = run.candidates.find(c => c.id === id)
      return c ? [{type: 'Feature', id: p.id, geometry: {type: 'Point', coordinates: [c.longitude, c.latitude]},
        properties: {label: p.label, status: 'confirmed_in_sample', sample: true, candidate_id: c.id,
          source_url: c.source_url, position_kind: c.position_kind, attribution: '© OpenStreetMap contributors',
          license_url: 'https://www.openstreetmap.org/copyright'}}] : []
    }),
    metadata: {sample: true, note: 'Illustrative UI sample. No model run. No accuracy measured.',
      unresolved: run.graph!.landmarks.filter(p => !run.confirmations[p.id]?.candidate_id).map(p => p.label),
      candidate_manifest: run.candidate_manifest},
  }
}
