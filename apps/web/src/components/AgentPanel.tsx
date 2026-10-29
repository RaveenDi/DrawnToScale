import {Bot} from 'lucide-react'
import type {Run} from '../types'

export default function AgentPanel({run}: {run: Run}) {
  const steps = run.trace ?? []
  if (!steps.length) return null
  return <section className="panel agent-panel" aria-label="Agent run">
    <div className="panel-heading"><span className="eyebrow">AGENT RUN</span><span className="agent-meta"><Bot size={14}/> {run.model_calls} model call{run.model_calls === 1 ? '' : 's'}</span></div>
    <ol className="agent-steps">{steps.map((s, i) => <li key={i} className={`agent-step status-${s.status}`}>
      <span className="agent-name">{s.agent}</span>
      <span className="agent-status">{s.status.replace('_', ' ')}</span>
      <span className="agent-time">{s.duration_ms} ms</span>
      <span className="agent-model">{s.model}</span>
      <span className="agent-out">{s.output_summary}</span>
    </li>)}</ol>
  </section>
}
