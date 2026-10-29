import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, query, type Page } from '../api';
import { Chips, Empty, ErrorNote, PageHead, Pager, SelectField, TextArea, TextField, Toggle, formatDate, useCan, useToast } from '../ui';

type Instructions = { enabled: boolean; house_style: string; features: Record<string, string> };
type InstructionsOut = {
  value: Instructions;
  features: { id: string; label: string }[];
  max_chars: number;
  updated_at: string | null;
  updated_by: string | null;
};
type Call = {
  created_at: string;
  provider: string;
  feature: string;
  model: string;
  ok: boolean;
  error: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  audio_seconds: number;
  cost_usd: number;
};
type FeatureStats = { feature: string; calls: number; failures: number; avg_latency_ms: number; cost_usd: number };
type CallsOut = Page<Call> & { week: FeatureStats[] };

const KEY = ['ai', 'instructions'] as const;
const LIMIT = 30;

/** AI & prompts: the coaching team's guidance for every AI feature, a try-it box, and the call log. */
export function Ai() {
  const instructions = useQuery({ queryKey: KEY, queryFn: () => api<InstructionsOut>('/ai/instructions') });
  return (
    <>
      <PageHead
        subtitle="Guide how the AI coaches, judges and debates. The built-in rules stay in place; your guidance is added to every request within a minute."
        title="AI & prompts"
      />
      <ErrorNote error={instructions.error} />
      {instructions.data ? <GuidanceEditor data={instructions.data} key={instructions.data.updated_at ?? 'default'} /> : null}
      <TryIt />
      <CallLog />
    </>
  );
}

function GuidanceEditor({ data }: { data: InstructionsOut }) {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [value, setValue] = useState<Instructions>(data.value);
  const dirty = JSON.stringify(value) !== JSON.stringify(data.value);
  const readOnly = !can.editContent;
  const save = useMutation({
    mutationFn: () => api<InstructionsOut>('/ai/instructions', { method: 'PUT', body: value }),
    onSuccess: (saved) => {
      queryClient.setQueryData(KEY, saved);
      toast('AI guidance saved. It applies to new requests within a minute.');
    },
  });

  return (
    <div className="card" style={{ display: 'grid', gap: 16 }}>
      <div className="card-head">
        <div>
          <h2>Coaching guidance</h2>
          <p className="muted" style={{ margin: '4px 0 0' }}>
            {data.updated_at ? `Last changed ${formatDate(data.updated_at)}${data.updated_by ? ` by ${data.updated_by}` : ''}.` : 'Using the built-in prompts only.'}
          </p>
        </div>
      </div>
      <Toggle
        checked={value.enabled}
        disabled={readOnly}
        hint="Off: every feature uses the app's built-in coach and judge instead, at no AI cost. Use this if AI spend runs too high or the AI misbehaves."
        label="AI coaching and judging on"
        onChange={(enabled) => setValue((current) => ({ ...current, enabled }))}
      />
      <TextArea
        disabled={readOnly}
        hint="Applies to every AI feature. Example: “Use Indian English and examples from Indian schools. Keep feedback warm and specific.”"
        label="House style (all features)"
        maxLength={data.max_chars}
        onChange={(house_style) => setValue((current) => ({ ...current, house_style }))}
        rows={3}
        value={value.house_style}
      />
      {data.features.map((feature) => (
        <TextArea
          disabled={readOnly}
          key={feature.id}
          label={feature.label}
          maxLength={data.max_chars}
          onChange={(text) => setValue((current) => ({ ...current, features: { ...current.features, [feature.id]: text } }))}
          rows={2}
          value={value.features[feature.id] ?? ''}
        />
      ))}
      <ErrorNote error={save.error} />
      {!readOnly ? (
        <div className="card-foot">
          <button className="btn btn-ghost" disabled={!dirty || save.isPending} onClick={() => setValue(data.value)} type="button">
            Undo changes
          </button>
          <button className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()} type="button">
            Save guidance
          </button>
        </div>
      ) : (
        <p className="muted">Only content managers and super admins can change the guidance.</p>
      )}
    </div>
  );
}

type TryOut = { feature: string; reply: string | null; analysis: Record<string, unknown> | null };

function TryIt() {
  const can = useCan();
  const [feature, setFeature] = useState<'debate.reply' | 'debate.analysis'>('debate.reply');
  const [motion, setMotion] = useState('Homework should be abolished in schools');
  const [text, setText] = useState(
    'Homework should be abolished because it adds stress and takes away sleep. Students learn more from reading and family time.',
  );
  const run = useMutation({
    mutationFn: () =>
      api<TryOut>('/ai/try', { method: 'POST', body: { feature, motion, student_text: text, student_side: 'for', difficulty: 'medium' } }),
  });
  if (!can.editContent) return null;
  return (
    <div className="card" style={{ display: 'grid', gap: 12, marginTop: 16 }}>
      <div className="card-head">
        <div>
          <h2>Try it</h2>
          <p className="muted" style={{ margin: '4px 0 0' }}>
            Runs a real AI request with the saved guidance on a sample student turn. Counts towards AI cost.
          </p>
        </div>
      </div>
      <SelectField
        label="Feature"
        onChange={(next) => setFeature(next as typeof feature)}
        options={[
          { value: 'debate.reply', label: 'Debate vs AI — the AI opponent’s reply' },
          { value: 'debate.analysis', label: 'Debate vs AI — the analysis report' },
        ]}
        value={feature}
      />
      <TextField label="Motion" maxLength={300} onChange={setMotion} value={motion} />
      <TextArea label="What the student said" maxLength={3000} onChange={setText} rows={3} value={text} />
      <ErrorNote error={run.error} />
      <div className="card-foot">
        <button className="btn btn-primary" disabled={run.isPending || motion.length < 5 || text.length < 10} onClick={() => run.mutate()} type="button">
          {run.isPending ? 'Asking the AI…' : 'Run'}
        </button>
      </div>
      {run.data?.reply ? <blockquote style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{run.data.reply}</blockquote> : null}
      {run.data?.analysis ? (
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12, maxHeight: 360, overflow: 'auto' }}>
          {JSON.stringify(run.data.analysis, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

function CallLog() {
  const [show, setShow] = useState<'all' | 'failed'>('all');
  const [offset, setOffset] = useState(0);
  const calls = useQuery({
    queryKey: ['ai', 'calls', show, offset],
    queryFn: () => api<CallsOut>(`/ai/calls${query({ failed: show === 'failed', limit: LIMIT, offset })}`),
    placeholderData: keepPreviousData,
  });
  return (
    <div style={{ display: 'grid', gap: 12, marginTop: 24 }}>
      <h2 style={{ margin: 0 }}>AI requests</h2>
      {calls.data?.week.length ? (
        <div className="card table-card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Feature (last 7 days)</th>
                  <th>Requests</th>
                  <th>Failed</th>
                  <th>Avg time</th>
                  <th>Est. cost</th>
                </tr>
              </thead>
              <tbody>
                {calls.data.week.map((row) => (
                  <tr key={row.feature}>
                    <td>{row.feature}</td>
                    <td>{row.calls}</td>
                    <td>{row.failures ? <strong>{row.failures}</strong> : 0}</td>
                    <td>{(row.avg_latency_ms / 1000).toFixed(1)} s</td>
                    <td>${row.cost_usd.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      <Chips
        label="Filter requests"
        onChange={(value) => {
          setShow(value);
          setOffset(0);
        }}
        options={[
          { value: 'all', label: 'All' },
          { value: 'failed', label: 'Failed only' },
        ]}
        value={show}
      />
      <ErrorNote error={calls.error} />
      {calls.data && !calls.data.items.length ? <Empty>No AI requests yet.</Empty> : null}
      {calls.data?.items.length ? (
        <div className="card table-card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Feature</th>
                  <th>Result</th>
                  <th>Time</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {calls.data.items.map((call, index) => (
                  <tr key={`${call.created_at}-${index}`}>
                    <td>{formatDate(call.created_at)}</td>
                    <td>
                      {call.feature}
                      <div className="muted" style={{ fontSize: 12 }}>{call.provider} · {call.model}</div>
                    </td>
                    <td>{call.ok ? 'OK' : <span title={call.error ?? ''}>Failed — {(call.error ?? '').slice(0, 80)}</span>}</td>
                    <td>{(call.latency_ms / 1000).toFixed(1)} s</td>
                    <td>${call.cost_usd.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {calls.data ? <Pager limit={LIMIT} offset={offset} onChange={setOffset} total={calls.data.total} /> : null}
    </div>
  );
}
