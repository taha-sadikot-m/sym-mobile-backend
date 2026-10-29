import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, query, type Page } from '../api';
import { useMe } from '../auth';
import { Empty, ErrorNote, PageHead, Pager, TextArea, TextField, formatDate, useToast } from '../ui';

type Row = {
  id: string;
  user_id: number | null;
  kind: string;
  title: string;
  body: string;
  devices: number;
  delivered: number;
  error: string | null;
  created_at: string;
};
type NotificationsOut = Page<Row> & { devices: number; students_with_devices: number; sent_week: number; failed_week: number };
type JobRun = { id: string; name: string; started_at: string; finished_at: string | null; ok: boolean; details: Record<string, unknown>; error: string | null };

const KINDS: Record<string, string> = {
  'challenge.received': 'Challenge received',
  'challenge.claimed': 'Invite opened',
  'challenge.accepted': 'Challenge accepted',
  'challenge.declined': 'Challenge declined',
  'challenge.countered': 'New time proposed',
  'challenge.cancelled': 'Debate cancelled',
  'challenge.lobby': 'Opponent in lobby',
  'challenge.reminder': '15-minute reminder',
  'result.ready': 'Verdict ready',
  announcement: 'Announcement',
};
const LIMIT = 30;

/** Notifications students received, devices registered, announcements, and the scheduled jobs. */
export function Notifications() {
  const [offset, setOffset] = useState(0);
  const list = useQuery({
    queryKey: ['notifications', offset],
    queryFn: () => api<NotificationsOut>(`/notifications${query({ limit: LIMIT, offset })}`),
    placeholderData: keepPreviousData,
  });
  const isSuper = useMe().data?.role === 'super_admin';
  const data = list.data;
  return (
    <>
      <PageHead
        subtitle="Push notifications for live debates (invites, replies, reminders, verdicts) and announcements to every student."
        title="Notifications"
      />
      <ErrorNote error={list.error} />
      {data ? (
        <div className="grid-4">
          <Stat label="Devices registered" value={data.devices} />
          <Stat label="Students reachable" value={data.students_with_devices} />
          <Stat label="Sent this week" value={data.sent_week} />
          <Stat label="Failed this week" value={data.failed_week} />
        </div>
      ) : null}
      {isSuper ? <Announce /> : null}
      <Jobs canRun={isSuper} />
      <h2 style={{ margin: '24px 0 12px' }}>Recent notifications</h2>
      {data && !data.items.length ? <Empty>Nothing sent yet.</Empty> : null}
      {data?.items.length ? (
        <div className="card table-card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Type</th>
                  <th>Message</th>
                  <th>Student</th>
                  <th>Devices</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDate(row.created_at)}</td>
                    <td>{KINDS[row.kind] ?? row.kind}</td>
                    <td>
                      <strong>{row.title}</strong>
                      <div className="muted" style={{ fontSize: 12 }}>{row.body}</div>
                      {row.error ? <div className="muted" style={{ fontSize: 12, color: 'var(--error)' }}>{row.error}</div> : null}
                    </td>
                    <td>{row.user_id ?? 'Everyone'}</td>
                    <td>
                      {row.delivered}/{row.devices}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {data ? <Pager limit={LIMIT} offset={offset} onChange={setOffset} total={data.total} /> : null}
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function Announce() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const send = useMutation({
    mutationFn: () => api('/notifications/announce', { method: 'POST', body: { title, body } }),
    onSuccess: async () => {
      setTitle('');
      setBody('');
      await queryClient.invalidateQueries({ queryKey: ['notifications'] });
      toast('Announcement sent.');
    },
  });
  return (
    <div className="card" style={{ display: 'grid', gap: 12, margin: '16px 0' }}>
      <h2 style={{ margin: 0 }}>Send an announcement</h2>
      <p className="muted" style={{ margin: 0 }}>Goes to every student with notifications on. Up to 5 a day; every one is logged.</p>
      <TextField label="Title" maxLength={60} onChange={setTitle} value={title} />
      <TextArea label="Message" maxLength={180} onChange={setBody} rows={2} value={body} />
      <ErrorNote error={send.error} />
      <div className="card-foot">
        <button
          className="btn btn-primary"
          disabled={send.isPending || title.length < 3 || body.length < 3}
          onClick={() => {
            if (window.confirm(`Send “${title}” to every student?`)) send.mutate();
          }}
          type="button"
        >
          Send to everyone
        </button>
      </div>
    </div>
  );
}

function Jobs({ canRun }: { canRun: boolean }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: ['jobs'], queryFn: () => api<JobRun[]>('/jobs'), refetchInterval: 60_000 });
  const run = useMutation({
    mutationFn: () => api<JobRun[]>('/jobs/run', { method: 'POST' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['jobs'] });
      toast('Jobs ran.');
    },
  });
  const latest = (name: string) => runs.data?.find((item) => item.name === name);
  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-head">
        <div>
          <h2>Scheduled jobs</h2>
          <p className="muted" style={{ margin: '4px 0 0' }}>
            Render runs these every 5 minutes: debate reminders, and once a day the data clean-up (expired codes, old sessions, logs past their retention).
          </p>
        </div>
        {canRun ? (
          <button className="btn btn-ghost btn-sm" disabled={run.isPending} onClick={() => run.mutate()} style={{ whiteSpace: 'nowrap' }} type="button">
            {run.isPending ? 'Running…' : 'Run now'}
          </button>
        ) : null}
      </div>
      <ErrorNote error={runs.error ?? run.error} />
      {(['reminders', 'cleanup'] as const).map((name) => {
        const item = latest(name);
        return (
          <div key={name}>
            <strong>{name === 'reminders' ? 'Reminders' : 'Clean-up'}</strong>{' '}
            <span className="muted">
              {item
                ? `${item.ok ? 'OK' : 'FAILED'} · ${formatDate(item.started_at)} · ${item.error ?? Object.entries(item.details).map(([key, value]) => `${key}: ${String(value)}`).join(', ')}`
                : 'Not run yet — set up the Render cron job (see docs/DEPLOY.md).'}
            </span>
          </div>
        );
      })}
    </div>
  );
}
