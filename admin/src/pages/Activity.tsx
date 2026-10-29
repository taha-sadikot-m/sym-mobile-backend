import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, query, type AuditEntry, type Page } from '../api';
import { Chips, Empty, ErrorNote, PageHead, Pager, formatDate } from '../ui';

const LIMIT = 50;

const AREAS = [
  { value: '', label: 'Everything' },
  { value: 'admin', label: 'Sign-ins' },
  { value: 'committee', label: 'Committees' },
  { value: 'agenda', label: 'Agendas' },
  { value: 'country', label: 'Countries' },
  { value: 'topic', label: 'Topics' },
  { value: 'setting', label: 'Settings' },
  { value: 'content', label: 'Imports' },
  { value: 'student', label: 'Students' },
  { value: 'report', label: 'Moderation' },
];

export const ACTION_LABELS: Record<string, string> = {
  'admin.signed_in': 'Signed in',
  'admin.signed_out': 'Signed out',
  'admin.sign_in_failed': 'Failed sign-in',
  'admin.sign_in_blocked': 'Sign-in blocked (locked)',
  'admin.created': 'Admin created',
  'committee.created': 'Created committee',
  'committee.updated': 'Edited committee',
  'committee.deleted': 'Deleted committee',
  'committee.reordered': 'Reordered committees',
  'committee.seats_updated': 'Changed seats',
  'agenda.created': 'Created agenda',
  'agenda.updated': 'Edited agenda',
  'agenda.deleted': 'Deleted agenda',
  'agenda.bulk_status': 'Changed agenda statuses',
  'country.created': 'Added country',
  'country.updated': 'Edited country',
  'topic.created': 'Created topic',
  'topic.updated': 'Edited topic',
  'topic.deleted': 'Deleted topic',
  'topic.bulk_status': 'Changed topic statuses',
  'setting.updated': 'Changed settings',
  'setting.reset': 'Reset settings',
  'content.imported': 'Imported content',
  'content.seeded': 'Loaded starting content',
  'student.suspended': 'Paused app access',
  'student.restored': 'Restored app access',
  'student.signed_out': 'Signed student out everywhere',
  'student.password_reset_sent': 'Sent password reset',
  'student.deleted': 'Deleted student account',
  'live.cancelled': 'Cancelled a live debate',
  'report.dismissed': 'Dismissed a report',
  'report.actioned': 'Actioned a report',
};

/** A one-line description of what was changed, from the audit details. */
export function describe(entry: AuditEntry): string {
  const details = entry.details;
  const name = (details.abbr ?? details.title ?? details.name ?? details.email ?? entry.target_id ?? '') as string;
  if (entry.action === 'agenda.bulk_status') return `${details.count} → ${String(details.status).replace('_', ' ')}`;
  if (entry.action === 'committee.seats_updated') {
    const added = (details.added as string[] | undefined)?.length ?? 0;
    const removed = (details.removed as string[] | undefined)?.length ?? 0;
    return `${name}: +${added} / −${removed}`;
  }
  return name;
}

function Changes({ changes }: { changes: unknown }) {
  if (!changes || typeof changes !== 'object') return null;
  const entries = Object.entries(changes as Record<string, [unknown, unknown]>);
  if (!entries.length) return null;
  const show = (value: unknown) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value);
    return text.length > 60 ? `${text.slice(0, 57)}…` : text;
  };
  return (
    <div className="diff">
      {entries.map(([field, [before, after]]) => (
        <div key={field}>
          <b>{field.replace(/_/g, ' ')}</b>: {show(before)} → {show(after)}
        </div>
      ))}
    </div>
  );
}

export function Activity() {
  const [area, setArea] = useState('');
  const [offset, setOffset] = useState(0);
  const activity = useQuery({
    queryKey: ['activity', area, offset],
    queryFn: () => api<Page<AuditEntry>>(`/activity${query({ area, limit: LIMIT, offset })}`),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHead subtitle="Every admin sign-in and change, newest first." title="Activity log" />
      <Chips
        label="Filter by area"
        onChange={(value) => {
          setArea(value);
          setOffset(0);
        }}
        options={AREAS}
        value={area}
      />
      <ErrorNote error={activity.error} />
      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Who</th>
                <th className="hide-sm">From</th>
              </tr>
            </thead>
            <tbody>
              {activity.data?.items.map((entry) => (
                <tr key={entry.id}>
                  <td>{formatDate(entry.created_at)}</td>
                  <td>
                    <div className="title">{ACTION_LABELS[entry.action] ?? entry.action}</div>
                    <div className="sub">{describe(entry)}</div>
                    <Changes changes={entry.details.changes} />
                  </td>
                  <td>{entry.admin_email ?? (entry.details.email as string | undefined) ?? 'System'}</td>
                  <td className="mono hide-sm">{entry.ip ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {activity.isPending ? <Empty>Loading…</Empty> : !activity.data?.items.length ? <Empty>No activity yet.</Empty> : null}
        {activity.data ? <Pager limit={LIMIT} offset={offset} onChange={setOffset} total={activity.data.total} /> : null}
      </div>
    </>
  );
}
