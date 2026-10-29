import {Bot} from 'lucide-react'
import type {Run} from '../types'

const fmt = (n: number | null | undefined) => n == null ? 'n/a' : n.toLocaleString()

export default function AgentPanel({run}: {run: Run}) {
  const steps = run.trace ?? []
  if (!steps.length) return null
  const t = run.totals
  const partial = run.agent && run.agent.status !== 'completed'
  return <section className="panel agent-panel" aria-label="Agent run">
    <div className="panel-heading"><span className="eyebrow">AGENT RUN</span><span className="agent-meta"><Bot size={14}/> {run.model_calls} model call{run.model_calls === 1 ? '' : 's'}</span></div>
    {partial && <p className="agent-banner" role="status">Partial result: {run.agent!.status.replace(/_/g, ' ')}. {run.agent!.detail}</p>}
    {t && <dl className="agent-totals" aria-label="Run totals">
      <div><dt>Turns / tools</dt><dd>{t.model_turns} / {t.tool_calls}</dd></div>
      <div><dt>Tokens in / out</dt><dd>{t.tokens_reported ? `${fmt(t.input_tokens)} / ${fmt(t.output_tokens)}` : 'not reported'}</dd></div>
      <div><dt>Model latency</dt><dd>{fmt(t.model_latency_ms)} ms</dd></div>
      <div><dt>Wall time</dt><dd>{fmt(t.wall_ms)} ms</dd></div>
      <div><dt>Est. cost</dt><dd>{t.est_cost_usd == null ? 'n/a' : `$${t.est_cost_usd.toFixed(4)}`}{t.est_cost_usd != null && t.cost_basis ? ' (UNVERIFIED prices)' : ''}</dd></div>
      {t.retries > 0 && <div><dt>HTTP retries</dt><dd>{t.retries}</dd></div>}
    </dl>}
    <ol className="agent-steps">{steps.map((s, i) => <li key={i} className={`agent-step status-${s.status} kind-${s.kind ?? 'step'}`}>
      <span className="agent-name">{s.kind === 'tool_call' ? `↳ ${s.tool}` : s.agent}</span>
      <span className="agent-status">{s.status.replace(/_/g, ' ')}</span>
      <span className="agent-time">{s.duration_ms} ms</span>
      <span className="agent-model">{s.telemetry ? `${s.telemetry.model}${s.telemetry.prompt_version ? ` · ${s.telemetry.prompt_version}` : ''}${s.telemetry.input_tokens != null ? ` · ${s.telemetry.input_tokens}/${s.telemetry.output_tokens} tok` : ''}${s.telemetry.retries ? ` · ${s.telemetry.retries} retr${s.telemetry.retries === 1 ? 'y' : 'ies'}` : ''}${s.telemetry.replayed ? ' · replayed' : ''}` : s.model}</span>
      <span className="agent-out">{s.output_summary}</span>
    </li>)}</ol>
  </section>
}
