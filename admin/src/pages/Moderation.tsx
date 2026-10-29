import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, query, type Page } from '../api';
import { Chips, Empty, ErrorNote, PageHead, Pager, TextArea, Toggle, formatDate, useToast } from '../ui';
import { DebateDrawer } from './Live';

type Person = { id: string; name: string; username: string; school: string };
type Report = {
  id: string;
  reason: string;
  details: string;
  status: 'open' | 'dismissed' | 'actioned';
  reporter: Person;
  reported: Person;
  reported_app_status: string | null;
  reports_against_reported: number;
  challenge_id: string | null;
  created_at: string;
  resolution_note: string | null;
};

const REASONS: Record<string, string> = {
  abuse: 'Abusive language',
  harassment: 'Harassment',
  inappropriate: 'Inappropriate content',
  cheating: 'Cheating',
  other: 'Other',
};
const LIMIT = 30;

export function useOpenReports() {
  return useQuery({
    queryKey: ['moderation', 'open-count'],
    queryFn: () => api<{ open: number }>('/moderation/open-count'),
    refetchInterval: 60_000,
  });
}

export function Moderation() {
  const [status, setStatus] = useState<'open' | 'actioned' | 'dismissed'>('open');
  const [offset, setOffset] = useState(0);
  const [debate, setDebate] = useState<string | null>(null);
  const reports = useQuery({
    queryKey: ['moderation', status, offset],
    queryFn: () => api<Page<Report>>(`/moderation/reports${query({ status, limit: LIMIT, offset })}`),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHead
        subtitle="Reports students made about each other in live debates. Read the debate, then dismiss or act."
        title="Moderation"
      />
      <Chips
        label="Filter reports"
        onChange={(value) => {
          setStatus(value);
          setOffset(0);
        }}
        options={[
          { value: 'open', label: 'Open' },
          { value: 'actioned', label: 'Actioned' },
          { value: 'dismissed', label: 'Dismissed' },
        ]}
        value={status}
      />
      <ErrorNote error={reports.error} />
      {reports.isPending ? <Empty>Loading…</Empty> : null}
      {reports.data && !reports.data.items.length ? <Empty>{status === 'open' ? 'Nothing to review. 🎉' : 'No reports here.'}</Empty> : null}
      <div style={{ display: 'grid', gap: 12 }}>
        {reports.data?.items.map((report) => (
          <ReportCard key={report.id} onOpenDebate={setDebate} report={report} />
        ))}
      </div>
      {reports.data ? <Pager limit={LIMIT} offset={offset} onChange={setOffset} total={reports.data.total} /> : null}
      {debate ? <DebateDrawer id={debate} onClose={() => setDebate(null)} /> : null}
    </>
  );
}

function ReportCard({ report, onOpenDebate }: { report: Report; onOpenDebate: (id: string) => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [note, setNote] = useState('');
  const [pause, setPause] = useState(false);
  const resolve = useMutation({
    mutationFn: (action: 'dismiss' | 'actioned') =>
      api(`/moderation/reports/${report.id}/resolve`, { method: 'POST', body: { action, note, pause_app_access: pause } }),
    onSuccess: async (_, action) => {
      await queryClient.invalidateQueries({ queryKey: ['moderation'] });
      await queryClient.invalidateQueries({ queryKey: ['students'] });
      toast(action === 'dismiss' ? 'Report dismissed.' : pause ? 'Actioned and app access paused.' : 'Report actioned.');
    },
  });

  return (
    <div className="card" style={{ display: 'grid', gap: 12 }}>
      <div className="card-head">
        <div>
          <h2>{REASONS[report.reason] ?? report.reason}</h2>
          <p className="muted" style={{ margin: '4px 0 0' }}>
            {report.reporter.name} reported <strong>{report.reported.name}</strong>
            {report.reported.username ? ` (@${report.reported.username})` : ''} · {formatDate(report.created_at)}
          </p>
        </div>
        <span className={`status ${report.reports_against_reported > 1 ? 'status-in_review' : 'status-draft'}`}>
          {report.reports_against_reported} report{report.reports_against_reported === 1 ? '' : 's'} against them
        </span>
      </div>
      {report.details ? <blockquote style={{ margin: 0, color: 'var(--text)' }}>“{report.details}”</blockquote> : null}
      {report.reported_app_status === 'suspended' ? <div className="alert alert-error">Their app access is already paused.</div> : null}
      {report.challenge_id ? (
        <div>
          <button className="btn btn-ghost btn-sm" onClick={() => onOpenDebate(report.challenge_id!)} type="button">
            Read the debate
          </button>
        </div>
      ) : null}
      <ErrorNote error={resolve.error} />
      {report.status === 'open' ? (
        <>
          <TextArea label="Note (shown in the activity log)" maxLength={1000} onChange={setNote} rows={2} value={note} />
          <Toggle checked={pause} hint="Signs them out and blocks the app until restored in Students." label="Also pause their app access" onChange={setPause} />
          <div className="card-foot">
            <button className="btn btn-ghost" disabled={resolve.isPending} onClick={() => resolve.mutate('dismiss')} type="button">
              Dismiss
            </button>
            <button className="btn btn-primary" disabled={resolve.isPending} onClick={() => resolve.mutate('actioned')} type="button">
              Take action
            </button>
          </div>
        </>
      ) : (
        <p className="muted">
          {report.status === 'dismissed' ? 'Dismissed' : 'Actioned'}
          {report.resolution_note ? ` — ${report.resolution_note}` : ''}
        </p>
      )}
    </div>
  );
}
