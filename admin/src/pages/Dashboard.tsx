import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { api, type AuditEntry, type Insights, type SystemStatus } from '../api';
import { Empty, formatDate } from '../ui';
import { ACTION_LABELS, describe } from './Activity';
import { useOpenReports } from './Moderation';
import { useStudentStats } from './Students';

export const STATUS_KEY = ['system-status'] as const;

export function useSystemStatus() {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => api<SystemStatus>('/system/status'),
    refetchInterval: 60_000,
  });
}

export function Dashboard() {
  const status = useSystemStatus();
  const data = status.data;
  const content = data?.content;
  const students = useStudentStats();
  const openReports = useOpenReports();
  const insights = useQuery({ queryKey: ['insights'], queryFn: () => api<Insights>('/insights'), refetchInterval: 60_000 });
  const practice = insights.data?.practice;
  const usage = insights.data?.ai;

  return (
    <>
      <div className="page-head">
        <h1>Dashboard</h1>
        <p>Content and system health. Student and session statistics arrive with each phase.</p>
      </div>

      {status.isError ? (
        <div className="alert alert-error" role="alert">
          Couldn’t load the system status. {status.error.message}
        </div>
      ) : null}

      {openReports.data && openReports.data.open > 0 ? (
        <div className="card cta">
          <div>
            <h2>
              {openReports.data.open} report{openReports.data.open === 1 ? '' : 's'} waiting for moderation
            </h2>
            <p className="muted">Students reported someone in a live debate. Review them promptly.</p>
          </div>
          <Link className="btn btn-primary" to="/moderation">
            Review reports
          </Link>
        </div>
      ) : null}

      {content && content.agendas_in_review > 0 ? (
        <div className="card cta">
          <div>
            <h2>{content.agendas_in_review} agendas waiting for review</h2>
            <p className="muted">They have SYM-written caucus topics and frameworks. Check them, then publish.</p>
          </div>
          <Link className="btn btn-primary" to="/agendas?status=in_review">
            Review agendas
          </Link>
        </div>
      ) : null}

      <div className="grid-4">
        <Stat label="App students" value={students.data?.total} />
        <Stat label="Joined this week" value={students.data?.new_this_week} />
        <Stat label="Active this week" value={students.data?.active_this_week} />
        <Stat label="Paused" value={students.data?.suspended} />
      </div>

      {usage?.over_budget ? (
        <div className="alert alert-error" role="alert">
          AI spend today (${usage.cost_today_usd.toFixed(2)}) is above the daily budget of ${usage.daily_budget_usd.toFixed(2)}.
        </div>
      ) : null}

      <div className="grid-4">
        <Stat
          label="Practice this week"
          sub={practice ? `${practice.sessions_this_week} MUN · ${practice.debates_this_week} debates` : undefined}
          value={practice ? practice.sessions_this_week + practice.debates_this_week : undefined}
        />
        <Stat label="Students practising" sub="this week" value={practice?.students_practising_this_week} />
        <Stat label="Average score" value={practice?.average_score ?? '—'} />
        <Stat label="AI spend today" sub={usage ? `$${usage.cost_week_usd.toFixed(2)} this week` : undefined} value={usage ? `$${usage.cost_today_usd.toFixed(2)}` : undefined} />
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Most practised committees</h2>
            <span className="mono">{practice ? `${practice.ai_feedback_share}% scored by the AI coach` : ''}</span>
          </div>
          {!practice?.top_committees.length ? (
            <Empty>No practice sessions yet.</Empty>
          ) : (
            <table>
              <tbody>
                {practice.top_committees.map((item) => (
                  <tr key={item.committee}>
                    <td>{item.committee}</td>
                    <td className="num">{item.sessions} sessions</td>
                    <td className="num">avg {item.average_score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <div className="card-head">
            <h2>AI coach, last 7 days</h2>
            <span className="mono">{usage ? `${usage.calls_today} calls today, ${usage.failures_today} failed` : ''}</span>
          </div>
          {!usage?.by_feature.length ? (
            <Empty>No AI calls yet.</Empty>
          ) : (
            <table>
              <tbody>
                {usage.by_feature.map((item) => (
                  <tr key={item.feature}>
                    <td>{FEATURE_LABELS[item.feature] ?? item.feature}</td>
                    <td className="num">{item.calls}</td>
                    <td className="num">{item.failures ? <span className="status status-in_review">{item.failures} failed</span> : 'all ok'}</td>
                    <td className="num mono">{(item.average_ms / 1000).toFixed(1)} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="grid-4">
        <Stat label="Committees live" sub={content ? `of ${content.committees_total}` : undefined} value={content?.committees_published} />
        <Stat label="Agendas live" sub={content ? `of ${content.agendas_total}` : undefined} value={content?.agendas_published} />
        <Stat label="Countries" value={content?.countries_active} />
        <Stat label="Debate topics live" value={content?.topics_published} />
      </div>

      <div className="grid-4">
        <Stat label="Environment" value={data?.environment} />
        <Stat label="Version" value={data?.version} />
        <div className="card">
          <div className="stat-label">Web tables</div>
          <div className="stat-value">
            {data ? (
              <span className={`pill ${data.web_tables_ok ? 'pill-ok' : 'pill-bad'}`}>
                <span className="dot" />
                {data.web_tables_ok ? 'In sync' : 'Changed'}
              </span>
            ) : (
              '—'
            )}
          </div>
        </div>
        <Stat label="Active admins" value={data?.active_admins} />
      </div>

      {data && !data.web_tables_ok ? (
        <div className="card">
          <div className="card-head">
            <h2>Web tables changed</h2>
          </div>
          <p>
            The web backend changed tables this service depends on. Writes to them are unsafe until the mobile backend is
            updated.
          </p>
          <ul>
            {data.web_table_changes.map((change) => (
              <li className="mono" key={change}>
                {change}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <RecentActivity entries={data?.recent_activity} loading={status.isPending} />
    </>
  );
}

const FEATURE_LABELS: Record<string, string> = {
  'mun.opponents': 'Choosing opponents',
  'mun.speech': 'Speech analysis',
  'mun.poi_answer': 'Answer transcription',
  'mun.pois': 'Writing POIs',
  'mun.feedback': 'MUN feedback reports',
  'debate.reply': 'Debate opponent replies',
  'debate.analysis': 'Debate judging',
  'debate.turn': 'Debate turn transcription',
  'live.judge': 'Live debate judging',
};

function Stat({ label, value, sub }: { label: string; value: number | string | undefined; sub?: string }) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">
        {value ?? '—'} {sub ? <span className="muted" style={{ fontSize: 14, fontFamily: 'var(--font)' }}>{sub}</span> : null}
      </div>
    </div>
  );
}

function RecentActivity({ entries, loading }: { entries: AuditEntry[] | undefined; loading: boolean }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>Recent activity</h2>
        <Link to="/activity">See all</Link>
      </div>
      {loading ? (
        <Empty>Loading…</Empty>
      ) : !entries?.length ? (
        <Empty>No activity yet.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Who</th>
              </tr>
            </thead>
            <tbody>
              {entries.slice(0, 10).map((entry) => (
                <tr key={entry.id}>
                  <td>{formatDate(entry.created_at)}</td>
                  <td>
                    {ACTION_LABELS[entry.action] ?? entry.action}
                    {describe(entry) ? <span className="muted"> · {describe(entry)}</span> : null}
                  </td>
                  <td>{entry.admin_email ?? (entry.details.email as string | undefined) ?? 'System'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
