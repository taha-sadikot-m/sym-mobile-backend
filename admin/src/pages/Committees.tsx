import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { api, SEAT_TYPE_LABELS, type Committee, type Difficulty, type SeatType, type Status } from '../api';
import {
  Chips,
  Drawer,
  Empty,
  ErrorNote,
  NumberField,
  PageHead,
  SearchInput,
  SelectField,
  StatusBadge,
  StatusSelect,
  TextArea,
  TextField,
  useCan,
  useToast,
} from '../ui';

export const COMMITTEES_KEY = ['committees'] as const;

export function useCommittees() {
  return useQuery({ queryKey: COMMITTEES_KEY, queryFn: () => api<Committee[]>('/committees') });
}

type Form = {
  abbr: string;
  name: string;
  category: string;
  seat_type: SeatType;
  difficulty: Difficulty;
  description: string;
  decision_rule: string;
  binding: string;
  typical_seats: number | null;
  status: Status;
};

const EMPTY: Form = {
  abbr: '',
  name: '',
  category: 'UN body',
  seat_type: 'country',
  difficulty: 'Beginner',
  description: '',
  decision_rule: '',
  binding: '',
  typical_seats: null,
  status: 'draft',
};

const toForm = (committee: Committee): Form => ({
  abbr: committee.abbr,
  name: committee.name,
  category: committee.category,
  seat_type: committee.seat_type,
  difficulty: committee.difficulty,
  description: committee.description,
  decision_rule: committee.decision_rule,
  binding: committee.binding,
  typical_seats: committee.typical_seats,
  status: committee.status,
});

type StatusFilter = 'all' | Status;

export function Committees() {
  const committees = useCommittees();
  const can = useCan();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [editing, setEditing] = useState<Committee | 'new' | null>(null);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (committees.data ?? []).filter(
      (item) =>
        (status === 'all' || item.status === status) &&
        (!needle || `${item.abbr} ${item.name} ${item.category}`.toLowerCase().includes(needle)),
    );
  }, [committees.data, q, status]);

  const count = (value: Status) => committees.data?.filter((item) => item.status === value).length ?? 0;

  return (
    <>
      <PageHead
        actions={
          can.editContent ? (
            <button className="btn btn-primary" onClick={() => setEditing('new')} type="button">
              New committee
            </button>
          ) : null
        }
        subtitle="The bodies students can simulate. Committees appear in the app when published and they have at least one published agenda."
        title="Committees"
      />
      <div className="toolbar">
        <SearchInput onChange={setQ} placeholder="Search committees" value={q} />
        <Chips<StatusFilter>
          label="Filter by status"
          onChange={setStatus}
          options={[
            { value: 'all', label: 'All', count: committees.data?.length },
            { value: 'published', label: 'Published', count: count('published') },
            { value: 'draft', label: 'Draft', count: count('draft') },
            { value: 'archived', label: 'Archived', count: count('archived') },
          ]}
          value={status}
        />
      </div>
      <ErrorNote error={committees.error} />
      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Committee</th>
                <th className="hide-sm">Seats</th>
                <th className="hide-sm">Level</th>
                <th className="num">Agendas</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr className="clickable" key={item.id} onClick={() => setEditing(item)}>
                  <td>
                    <div className="title">{item.abbr}</div>
                    <div className="sub">{item.name}</div>
                  </td>
                  <td className="hide-sm">
                    <div>{SEAT_TYPE_LABELS[item.seat_type]}</div>
                    <div className="sub">{item.seat_rule === 'selected' ? `${item.seat_count} seated countries` : 'All UN members'}</div>
                  </td>
                  <td className="hide-sm">{item.difficulty}</td>
                  <td className="num">
                    <Link onClick={(event) => event.stopPropagation()} to={`/agendas?committee=${item.id}`}>
                      {item.published_agenda_count} / {item.agenda_count}
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {committees.isPending ? <Empty>Loading…</Empty> : !rows.length ? <Empty>No committees match.</Empty> : null}
      </div>
      {editing ? <CommitteeDrawer committee={editing === 'new' ? null : editing} onClose={() => setEditing(null)} /> : null}
    </>
  );
}

function CommitteeDrawer({ committee, onClose }: { committee: Committee | null; onClose: () => void }) {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<Form>(committee ? toForm(committee) : EMPTY);
  const set = <K extends keyof Form>(key: K) => (value: Form[K]) => setForm((current) => ({ ...current, [key]: value }));
  const readOnly = !can.editContent;

  const save = useMutation({
    mutationFn: () =>
      committee
        ? api<Committee>(`/committees/${committee.id}`, { method: 'PATCH', body: form })
        : api<Committee>('/committees', { method: 'POST', body: form }),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: COMMITTEES_KEY });
      toast(committee ? `Saved ${saved.abbr}.` : `Created ${saved.abbr}.`);
      onClose();
    },
  });

  const remove = useMutation({
    mutationFn: () => api(`/committees/${committee?.id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: COMMITTEES_KEY });
      toast(`Deleted ${committee?.abbr}.`);
      onClose();
    },
  });

  return (
    <Drawer
      footer={
        readOnly ? (
          <span className="muted">Your role can view committees but not change them.</span>
        ) : (
          <>
            {committee ? (
              <button
                className="btn btn-danger"
                disabled={remove.isPending}
                onClick={() => {
                  if (window.confirm(`Delete ${committee.abbr}? This can’t be undone.`)) remove.mutate();
                }}
                type="button"
              >
                Delete
              </button>
            ) : null}
            <span className="spacer" />
            <button className="btn btn-ghost" onClick={onClose} type="button">
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={save.isPending || !form.abbr.trim() || form.name.trim().length < 2}
              onClick={() => save.mutate()}
              type="button"
            >
              {save.isPending ? 'Saving…' : committee ? 'Save changes' : 'Create committee'}
            </button>
          </>
        )
      }
      onClose={onClose}
      subtitle={committee ? `${committee.agenda_count} agendas · ${committee.slug}` : 'Starts as a draft until you publish it.'}
      title={committee ? committee.abbr : 'New committee'}
    >
      <ErrorNote error={save.error ?? remove.error} />
      <div className="row-2">
        <TextField disabled={readOnly} label="Abbreviation" maxLength={32} onChange={set('abbr')} required value={form.abbr} />
        <TextField disabled={readOnly} label="Category" maxLength={64} onChange={set('category')} value={form.category} />
      </div>
      <TextField disabled={readOnly} label="Full name" maxLength={160} onChange={set('name')} required value={form.name} />
      <StatusSelect disabled={readOnly} onChange={set('status')} value={form.status} />
      <div className="row-2">
        <SelectField
          disabled={readOnly}
          hint={form.seat_type !== 'country' ? 'The app doesn’t support this role type yet — keep it as a draft.' : undefined}
          label="Delegates represent"
          onChange={(value) => set('seat_type')(value as SeatType)}
          options={Object.entries(SEAT_TYPE_LABELS).map(([value, label]) => ({ value, label }))}
          value={form.seat_type}
        />
        <SelectField
          disabled={readOnly}
          label="Level"
          onChange={(value) => set('difficulty')(value as Difficulty)}
          options={['Beginner', 'Intermediate', 'Advanced'].map((value) => ({ value, label: value }))}
          value={form.difficulty}
        />
      </div>
      <NumberField disabled={readOnly} label="Typical seats" max={500} min={1} onChange={set('typical_seats')} value={form.typical_seats} />
      <TextArea disabled={readOnly} label="What the real body is" maxLength={2000} onChange={set('description')} value={form.description} />
      <TextArea disabled={readOnly} label="How it decides" maxLength={1000} onChange={set('decision_rule')} rows={2} value={form.decision_rule} />
      <TextField disabled={readOnly} label="Are decisions binding?" maxLength={500} onChange={set('binding')} value={form.binding} />
      {committee ? (
        <p className="muted">
          Seats are managed in <Link to={`/seats?committee=${committee.id}`}>Committee seats</Link>; agendas in{' '}
          <Link to={`/agendas?committee=${committee.id}`}>Agendas</Link>.
        </p>
      ) : null}
    </Drawer>
  );
}
