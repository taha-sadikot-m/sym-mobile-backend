import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, query, type Page } from '../api';
import { useMe } from '../auth';
import { Chips, Drawer, Empty, ErrorNote, PageHead, Pager, formatDate, useToast } from '../ui';

type Row = {
  id: string;
  code: string;
  topic: string;
  format: string;
  status: string;
  challenger: string;
  opponent: string | null;
  scheduled_at: string;
  created_at: string;
  winner: string | null;
};

type Turn = { id: string; slotIndex: number; speakerId: string; side: string; phase: string; role: string; text: string; usedMs: number; allowedMs: number };
type Person = { id: string; name: string; username: string; school: string };
type Detail = {
  challenge: { id: string; topic: { title: string }; format: string; status: string; challenger: Person; opponent: Person; challengerSide: string | null; scheduledAt: string };
  turns: Turn[];
  events: { kind: string; slot: number | null; user: string | null; at: string }[];
  result: {
    judge: { winnerId: string; margin: number; reasons: string[]; scores: { userId: string; total: number }[] };
    judged_by: string;
  } | null;
};

const LIMIT = 50;
const FORMAT_NAMES: Record<string, string> = { quick: 'Quick Clash', standard: 'Standard', rapid: 'Rapid Fire' };
const STATUS_TONE: Record<string, string> = {
  pending: 'status-in_review',
  countered: 'status-in_review',
  accepted: 'status-draft',
  live: 'status-published',
  completed: 'status-archived',
  declined: 'status-draft',
  cancelled: 'status-draft',
  expired: 'status-draft',
};

export function LiveDebates() {
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ['live', status, offset],
    queryFn: () => api<Page<Row>>(`/live/challenges${query({ status, limit: LIMIT, offset })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
  const role = useMe().data?.role;
  const canOpen = role === 'super_admin' || role === 'moderator';

  return (
    <>
      <PageHead subtitle="Challenges between students and the debates that came from them." title="Live debates" />
      <Chips
        label="Filter by status"
        onChange={(value) => {
          setStatus(value);
          setOffset(0);
        }}
        options={[
          { value: '', label: 'All' },
          { value: 'live', label: 'Live now' },
          { value: 'accepted', label: 'Upcoming' },
          { value: 'pending', label: 'Waiting' },
          { value: 'completed', label: 'Finished' },
          { value: 'cancelled', label: 'Cancelled' },
        ]}
        value={status}
      />
      <ErrorNote error={list.error} />
      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Motion</th>
                <th className="hide-sm">Debaters</th>
                <th className="hide-sm">When</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr className={canOpen ? 'clickable' : ''} key={row.id} onClick={() => canOpen && setOpen(row.id)}>
                  <td>
                    <div className="title">{row.topic}</div>
                    <div className="sub">
                      {FORMAT_NAMES[row.format] ?? row.format} · code {row.code}
                      {row.winner ? ` · won by ${row.winner}` : ''}
                    </div>
                  </td>
                  <td className="hide-sm">
                    {row.challenger} <span className="muted">vs</span> {row.opponent ?? <span className="muted">open invite</span>}
                  </td>
                  <td className="hide-sm">{formatDate(row.scheduled_at)}</td>
                  <td>
                    <span className={`status ${STATUS_TONE[row.status] ?? 'status-draft'}`}>{row.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {list.isPending ? <Empty>Loading…</Empty> : !list.data?.items.length ? <Empty>No debates here yet.</Empty> : null}
        {list.data ? <Pager limit={LIMIT} offset={offset} onChange={setOffset} total={list.data.total} /> : null}
      </div>
      {open ? <DebateDrawer id={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

export function DebateDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ['live-detail', id], queryFn: () => api<Detail>(`/live/challenges/${id}`) });
  const cancel = useMutation({
    mutationFn: () => api(`/live/challenges/${id}/cancel`, { method: 'POST' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['live'] });
      await queryClient.invalidateQueries({ queryKey: ['live-detail', id] });
      toast('Debate cancelled.');
    },
  });
  const data = detail.data;
  const names: Record<string, string> = data
    ? { [data.challenge.challenger.id]: data.challenge.challenger.name, [data.challenge.opponent.id]: data.challenge.opponent.name }
    : {};

  return (
    <Drawer
      footer={
        data && !['completed', 'cancelled', 'declined', 'expired'].includes(data.challenge.status) ? (
          <>
            <span className="spacer" />
            <button
              className="btn btn-danger"
              disabled={cancel.isPending}
              onClick={() => window.confirm('Cancel this debate for both students?') && cancel.mutate()}
              type="button"
            >
              Cancel debate
            </button>
          </>
        ) : undefined
      }
      onClose={onClose}
      subtitle={data ? `${FORMAT_NAMES[data.challenge.format] ?? data.challenge.format} · ${data.challenge.status}` : undefined}
      title={data?.challenge.topic.title ?? 'Debate'}
    >
      <ErrorNote error={detail.error ?? cancel.error} />
      {!data ? (
        <Empty>Loading…</Empty>
      ) : (
        <>
          <div className="card">
            <dl className="facts">
              <dt>For</dt>
              <dd>{data.challenge.challengerSide === 'against' ? data.challenge.opponent.name : data.challenge.challenger.name}</dd>
              <dt>Against</dt>
              <dd>{data.challenge.challengerSide === 'against' ? data.challenge.challenger.name : data.challenge.opponent.name}</dd>
              <dt>Scheduled</dt>
              <dd>{formatDate(data.challenge.scheduledAt)}</dd>
              {data.result ? (
                <>
                  <dt>Winner</dt>
                  <dd>
                    {names[data.result.judge.winnerId] ?? '—'} by {data.result.judge.margin} points (
                    {data.result.judged_by === 'ai' ? 'AI judge' : 'built-in judge'})
                  </dd>
                </>
              ) : null}
            </dl>
          </div>
          {data.result?.judge.reasons.length ? (
            <div className="card">
              <h3>Why</h3>
              {data.result.judge.reasons.map((reason) => (
                <p key={reason}>{reason}</p>
              ))}
            </div>
          ) : null}
          <div className="card" style={{ display: 'grid', gap: 12 }}>
            <h3>Transcript</h3>
            {!data.turns.length ? <p className="muted">No turns were recorded.</p> : null}
            {[...data.turns]
              .sort((a, b) => a.slotIndex - b.slotIndex)
              .map((turn) => (
                <div key={turn.id}>
                  <div className="field-label">
                    {names[turn.speakerId] ?? 'Unknown'} · {turn.side} · {turn.phase} · {Math.round(turn.usedMs / 1000)}s
                  </div>
                  <p style={{ margin: '4px 0 0' }}>{turn.text}</p>
                </div>
              ))}
          </div>
          {data.events.length ? (
            <div className="card">
              <h3>Room events</h3>
              {data.events.map((event) => (
                <div className="mono" key={`${event.kind}-${event.at}`}>
                  {formatDate(event.at)} · {event.user} · {event.kind}
                  {event.slot !== null ? ` (turn ${event.slot + 1})` : ''}
                </div>
              ))}
            </div>
          ) : null}
        </>
      )}
    </Drawer>
  );
}
