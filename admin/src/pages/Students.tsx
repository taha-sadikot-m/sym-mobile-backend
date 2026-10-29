import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, query, type Page } from '../api';
import { useMe } from '../auth';
import { Chips, Drawer, Empty, ErrorNote, PageHead, Pager, SearchInput, TextArea, formatDate, useDebounced, useToast } from '../ui';

export type Student = {
  id: number;
  full_name: string;
  email: string;
  username: string | null;
  age: number | null;
  institution: string;
  city: string;
  country: string;
  email_verified: boolean;
  account_active: boolean;
  app_status: 'active' | 'suspended';
  suspended_reason: string | null;
  joined_at: string;
  last_seen_at: string | null;
  active_sessions: number;
};

export type StudentStats = { total: number; new_this_week: number; active_this_week: number; suspended: number };

const LIMIT = 50;
type Filter = 'all' | 'active' | 'suspended' | 'unverified';

export function useStudentStats() {
  return useQuery({ queryKey: ['students', 'stats'], queryFn: () => api<StudentStats>('/students/stats') });
}

export function Students() {
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const search = useDebounced(q);
  const stats = useStudentStats();

  const students = useQuery({
    queryKey: ['students', search, filter, offset],
    queryFn: () =>
      api<Page<Student>>(
        `/students${query({
          q: search,
          app_status: filter === 'active' || filter === 'suspended' ? filter : '',
          verified: filter === 'unverified' ? false : '',
          limit: LIMIT,
          offset,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHead
        subtitle="Everyone who has used the app. Accounts are shared with the website, so a student signs in to both with the same email and password."
        title="Students"
      />
      <div className="grid-4">
        <Stat label="App students" value={stats.data?.total} />
        <Stat label="Joined this week" value={stats.data?.new_this_week} />
        <Stat label="Active this week" value={stats.data?.active_this_week} />
        <Stat label="Paused" value={stats.data?.suspended} />
      </div>
      <div className="toolbar">
        <SearchInput
          onChange={(value) => {
            setQ(value);
            setOffset(0);
          }}
          placeholder="Search name, email, @username or school"
          value={q}
        />
      </div>
      <Chips<Filter>
        label="Filter students"
        onChange={(value) => {
          setFilter(value);
          setOffset(0);
        }}
        options={[
          { value: 'all', label: 'All' },
          { value: 'active', label: 'Active' },
          { value: 'suspended', label: 'Paused' },
          { value: 'unverified', label: 'Email not confirmed' },
        ]}
        value={filter}
      />
      <ErrorNote error={students.error} />
      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Student</th>
                <th className="hide-sm">School</th>
                <th className="hide-sm">Joined</th>
                <th>Last active</th>
              </tr>
            </thead>
            <tbody>
              {students.data?.items.map((student) => (
                <tr className="clickable" key={student.id} onClick={() => setOpen(student.id)}>
                  <td>
                    <div className="title">
                      {student.full_name} {student.username ? <span className="muted">@{student.username}</span> : null}
                    </div>
                    <div className="sub">
                      {student.email}
                      {student.app_status === 'suspended' ? <span className="status status-archived" style={{ marginLeft: 6 }}>Paused</span> : null}
                      {!student.email_verified ? <span className="status status-in_review" style={{ marginLeft: 6 }}>Unconfirmed</span> : null}
                    </div>
                  </td>
                  <td className="hide-sm">
                    <div>{student.institution || '—'}</div>
                    <div className="sub">{[student.city, student.country].filter(Boolean).join(', ')}</div>
                  </td>
                  <td className="hide-sm">{formatDate(student.joined_at)}</td>
                  <td>{formatDate(student.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {students.isPending ? <Empty>Loading…</Empty> : !students.data?.items.length ? <Empty>No students match.</Empty> : null}
        {students.data ? <Pager limit={LIMIT} offset={offset} onChange={setOffset} total={students.data.total} /> : null}
      </div>
      {open !== null ? <StudentDrawer id={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function Stat({ label, value }: { label: string; value: number | undefined }) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value ?? '—'}</div>
    </div>
  );
}

function StudentDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const role = useMe().data?.role;
  const canModerate = role === 'super_admin' || role === 'moderator';
  const canDelete = role === 'super_admin';
  const toast = useToast();
  const queryClient = useQueryClient();
  const student = useQuery({ queryKey: ['student', id], queryFn: () => api<Student>(`/students/${id}`) });
  const [reason, setReason] = useState('');

  const refresh = async (updated?: Student) => {
    if (updated) queryClient.setQueryData(['student', id], updated);
    await queryClient.invalidateQueries({ queryKey: ['students'] });
  };
  const suspend = useMutation({
    mutationFn: () => api<Student>(`/students/${id}/suspend`, { method: 'POST', body: { reason } }),
    onSuccess: async (updated) => {
      await refresh(updated);
      setReason('');
      toast('App access paused and the student was signed out.');
    },
  });
  const restore = useMutation({
    mutationFn: () => api<Student>(`/students/${id}/restore`, { method: 'POST' }),
    onSuccess: async (updated) => {
      await refresh(updated);
      toast('App access restored.');
    },
  });
  const signOut = useMutation({
    mutationFn: () => api(`/students/${id}/sign-out`, { method: 'POST' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['student', id] });
      toast('Signed out on every device.');
    },
  });
  const reset = useMutation({
    mutationFn: () => api<{ message: string }>(`/students/${id}/password-reset`, { method: 'POST' }),
    onSuccess: (result) => toast(result.message),
  });
  const remove = useMutation({
    mutationFn: () => api(`/students/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      await refresh();
      toast('Account deleted.');
      onClose();
    },
  });

  const data = student.data;
  return (
    <Drawer onClose={onClose} subtitle={data ? data.email : undefined} title={data?.full_name ?? 'Student'}>
      <ErrorNote error={student.error ?? suspend.error ?? restore.error ?? signOut.error ?? reset.error ?? remove.error} />
      {data ? (
        <>
          <div className="card">
            <dl className="facts">
              <dt>Username</dt>
              <dd>{data.username ? `@${data.username}` : '—'}</dd>
              <dt>Age</dt>
              <dd>{data.age ?? '—'}</dd>
              <dt>School</dt>
              <dd>{data.institution || '—'}</dd>
              <dt>Location</dt>
              <dd>{[data.city, data.country].filter(Boolean).join(', ') || '—'}</dd>
              <dt>Email</dt>
              <dd>{data.email_verified ? 'Confirmed' : 'Not confirmed yet'}</dd>
              <dt>Joined</dt>
              <dd>{formatDate(data.joined_at)}</dd>
              <dt>Last active</dt>
              <dd>{formatDate(data.last_seen_at)}</dd>
              <dt>Signed-in devices</dt>
              <dd>{data.active_sessions}</dd>
              <dt>App access</dt>
              <dd>{data.app_status === 'active' ? 'Active' : `Paused — ${data.suspended_reason ?? ''}`}</dd>
            </dl>
          </div>

          {canModerate ? (
            <div className="card" style={{ display: 'grid', gap: 12 }}>
              <h3>Moderation</h3>
              {data.app_status === 'active' ? (
                <>
                  <TextArea
                    hint="Visible in the activity log. The student’s website account isn’t affected."
                    label="Reason for pausing app access"
                    maxLength={500}
                    onChange={setReason}
                    rows={2}
                    value={reason}
                  />
                  <button
                    className="btn btn-danger"
                    disabled={reason.trim().length < 3 || suspend.isPending}
                    onClick={() => suspend.mutate()}
                    type="button"
                  >
                    Pause app access
                  </button>
                </>
              ) : (
                <button className="btn btn-primary" disabled={restore.isPending} onClick={() => restore.mutate()} type="button">
                  Restore app access
                </button>
              )}
              <div className="toolbar">
                <button className="btn btn-ghost" disabled={signOut.isPending || !data.active_sessions} onClick={() => signOut.mutate()} type="button">
                  Sign out everywhere
                </button>
                <button className="btn btn-ghost" disabled={reset.isPending} onClick={() => reset.mutate()} type="button">
                  Email a password reset code
                </button>
              </div>
            </div>
          ) : null}

          {canDelete ? (
            <div className="card" style={{ display: 'grid', gap: 10 }}>
              <h3>Delete account</h3>
              <p className="muted">
                Removes the student’s personal data from the app and website (their sign-in stops working everywhere). This can’t be undone.
              </p>
              <button
                className="btn btn-danger"
                disabled={remove.isPending}
                onClick={() => window.confirm(`Delete ${data.full_name}'s account everywhere? This can’t be undone.`) && remove.mutate()}
                type="button"
              >
                Delete account
              </button>
            </div>
          ) : null}
        </>
      ) : (
        <Empty>Loading…</Empty>
      )}
    </Drawer>
  );
}
