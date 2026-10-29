import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useSearchParams } from 'react-router';
import { api, query, upload, type Agenda, type ImportResult, type Page, type Status } from '../api';
import {
  Chips,
  Drawer,
  Empty,
  ErrorNote,
  ListEditor,
  PageHead,
  Pager,
  SearchInput,
  SelectField,
  StatusBadge,
  StatusSelect,
  TextField,
  Toggle,
  useCan,
  useDebounced,
  useToast,
} from '../ui';
import { COMMITTEES_KEY, useCommittees } from './Committees';

const LIMIT = 50;
type StatusFilter = 'all' | Status;

export function Agendas() {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const committees = useCommittees();
  const [params, setParams] = useSearchParams();
  const committee = params.get('committee') ?? '';
  const status = (params.get('status') ?? 'all') as StatusFilter;
  const [q, setQ] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<Agenda | 'new' | null>(null);
  const [importing, setImporting] = useState(false);
  const search = useDebounced(q);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value && value !== 'all') next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
    setOffset(0);
    setSelected(new Set());
  };

  const agendas = useQuery({
    queryKey: ['agendas', { committee, status, search, offset }],
    queryFn: () =>
      api<Page<Agenda>>(
        `/agendas${query({ committee_id: committee, status: status === 'all' ? '' : status, q: search, limit: LIMIT, offset })}`,
      ),
    placeholderData: keepPreviousData,
  });

  const bulk = useMutation({
    mutationFn: (next: Status) =>
      api<{ updated: number }>('/agendas/bulk-status', { method: 'POST', body: { ids: [...selected], status: next } }),
    onSuccess: async (result, next) => {
      setSelected(new Set());
      await queryClient.invalidateQueries({ queryKey: ['agendas'] });
      await queryClient.invalidateQueries({ queryKey: COMMITTEES_KEY });
      toast(`${result.updated} agenda${result.updated === 1 ? '' : 's'} moved to ${next.replace('_', ' ')}.`);
    },
    onError: (error) => toast(error.message, 'error'),
  });

  const items = agendas.data?.items ?? [];
  const allOnPage = items.length > 0 && items.every((item) => selected.has(item.id));
  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <>
      <PageHead
        actions={
          can.editContent ? (
            <>
              <button className="btn btn-ghost" onClick={() => setImporting(true)} type="button">
                Import file
              </button>
              <button className="btn btn-primary" onClick={() => setEditing('new')} type="button">
                New agenda
              </button>
            </>
          ) : null
        }
        subtitle="Topics students debate in each committee. SYM-written caucus topics and frameworks arrive “In review” — check and publish them."
        title="Agendas"
      />
      <div className="toolbar">
        <SearchInput onChange={(value) => { setQ(value); setOffset(0); }} placeholder="Search agendas or themes" value={q} />
        <select aria-label="Committee" onChange={(event) => setFilter('committee', event.target.value)} value={committee}>
          <option value="">All committees</option>
          {committees.data?.map((item) => (
            <option key={item.id} value={item.id}>
              {item.abbr}
            </option>
          ))}
        </select>
      </div>
      <Chips<StatusFilter>
        label="Filter by status"
        onChange={(value) => setFilter('status', value)}
        options={[
          { value: 'all', label: 'All' },
          { value: 'in_review', label: 'In review' },
          { value: 'published', label: 'Published' },
          { value: 'draft', label: 'Draft' },
          { value: 'archived', label: 'Archived' },
        ]}
        value={status}
      />
      <ErrorNote error={agendas.error} />
      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {can.editContent ? (
                  <th className="check">
                    <input
                      aria-label="Select all on this page"
                      checked={allOnPage}
                      onChange={() =>
                        setSelected((current) => {
                          const next = new Set(current);
                          for (const item of items) {
                            if (allOnPage) next.delete(item.id);
                            else next.add(item.id);
                          }
                          return next;
                        })
                      }
                      type="checkbox"
                    />
                  </th>
                ) : null}
                <th>Agenda</th>
                <th className="hide-sm">Committee</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr className={`clickable ${selected.has(item.id) ? 'selected' : ''}`} key={item.id} onClick={() => setEditing(item)}>
                  {can.editContent ? (
                    <td className="check" onClick={(event) => event.stopPropagation()}>
                      <input aria-label={`Select ${item.title}`} checked={selected.has(item.id)} onChange={() => toggle(item.id)} type="checkbox" />
                    </td>
                  ) : null}
                  <td>
                    <div className="title">{item.title}</div>
                    <div className="sub">
                      {item.is_featured ? <span className="tag tag-gold">Featured</span> : null}
                      {item.theme ?? ''}
                      {item.caucus_topics.length ? ` · ${item.caucus_topics.length} caucus topics` : ''}
                    </div>
                  </td>
                  <td className="hide-sm">{item.committee_abbr}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {agendas.isPending ? <Empty>Loading…</Empty> : !items.length ? <Empty>No agendas match.</Empty> : null}
        {agendas.data ? <Pager limit={LIMIT} offset={offset} onChange={setOffset} total={agendas.data.total} /> : null}
      </div>

      {selected.size ? (
        <div className="bulkbar" role="region" aria-label="Bulk actions">
          <span>{selected.size} selected</span>
          <div>
            <button className="btn btn-primary btn-sm" disabled={bulk.isPending} onClick={() => bulk.mutate('published')} type="button">
              Publish
            </button>
            <button className="btn btn-ghost btn-sm" disabled={bulk.isPending} onClick={() => bulk.mutate('in_review')} type="button">
              Back to review
            </button>
            <button className="btn btn-ghost btn-sm" disabled={bulk.isPending} onClick={() => bulk.mutate('archived')} type="button">
              Archive
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())} type="button">
              Clear
            </button>
          </div>
        </div>
      ) : null}

      {editing ? (
        <AgendaDrawer agenda={editing === 'new' ? null : editing} defaultCommittee={committee} onClose={() => setEditing(null)} />
      ) : null}
      {importing ? <ImportDrawer onClose={() => setImporting(false)} /> : null}
    </>
  );
}

type Form = Pick<Agenda, 'committee_id' | 'title' | 'theme' | 'seen_at' | 'is_featured' | 'caucus_topics' | 'key_frameworks' | 'status'>;

function AgendaDrawer({ agenda, defaultCommittee, onClose }: { agenda: Agenda | null; defaultCommittee: string; onClose: () => void }) {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const committees = useCommittees();
  const readOnly = !can.editContent;
  const [form, setForm] = useState<Form>(
    agenda ?? {
      committee_id: defaultCommittee || committees.data?.[0]?.id || '',
      title: '',
      theme: '',
      seen_at: '',
      is_featured: false,
      caucus_topics: [],
      key_frameworks: [],
      status: 'draft',
    },
  );
  const set = <K extends keyof Form>(key: K) => (value: Form[K]) => setForm((current) => ({ ...current, [key]: value }));

  const done = async (message: string) => {
    await queryClient.invalidateQueries({ queryKey: ['agendas'] });
    await queryClient.invalidateQueries({ queryKey: COMMITTEES_KEY });
    toast(message);
    onClose();
  };
  const body = {
    ...form,
    theme: form.theme?.trim() || null,
    seen_at: form.seen_at?.trim() || null,
    caucus_topics: form.caucus_topics.map((item) => item.trim()).filter(Boolean),
    key_frameworks: form.key_frameworks.map((item) => item.trim()).filter(Boolean),
  };
  const save = useMutation({
    mutationFn: () =>
      agenda ? api<Agenda>(`/agendas/${agenda.id}`, { method: 'PATCH', body }) : api<Agenda>('/agendas', { method: 'POST', body }),
    onSuccess: () => done(agenda ? 'Agenda saved.' : 'Agenda created.'),
  });
  const remove = useMutation({
    mutationFn: () => api(`/agendas/${agenda?.id}`, { method: 'DELETE' }),
    onSuccess: () => done('Agenda deleted.'),
  });

  return (
    <Drawer
      footer={
        readOnly ? (
          <span className="muted">Your role can view agendas but not change them.</span>
        ) : (
          <>
            {agenda && agenda.status !== 'published' ? (
              <button
                className="btn btn-danger"
                disabled={remove.isPending}
                onClick={() => window.confirm('Delete this agenda? This can’t be undone.') && remove.mutate()}
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
              disabled={save.isPending || form.title.trim().length < 5 || !form.committee_id}
              onClick={() => save.mutate()}
              type="button"
            >
              {save.isPending ? 'Saving…' : agenda ? 'Save changes' : 'Create agenda'}
            </button>
          </>
        )
      }
      onClose={onClose}
      subtitle={agenda ? `${agenda.committee_abbr} · source: ${agenda.kind}` : 'Starts as a draft unless you publish it.'}
      title={agenda ? 'Edit agenda' : 'New agenda'}
    >
      <ErrorNote error={save.error ?? remove.error} />
      <SelectField
        disabled={readOnly}
        label="Committee"
        onChange={set('committee_id')}
        options={(committees.data ?? []).map((item) => ({ value: item.id, label: `${item.abbr} — ${item.name}` }))}
        value={form.committee_id}
      />
      <TextField disabled={readOnly} label="Agenda" maxLength={300} onChange={set('title')} required value={form.title} />
      <StatusSelect disabled={readOnly} onChange={set('status')} value={form.status} />
      <div className="row-2">
        <TextField disabled={readOnly} label="Theme" maxLength={120} onChange={set('theme')} value={form.theme ?? ''} />
        <TextField disabled={readOnly} label="Seen at (conference)" maxLength={200} onChange={set('seen_at')} value={form.seen_at ?? ''} />
      </div>
      <Toggle
        checked={form.is_featured}
        disabled={readOnly}
        hint="Featured agendas are listed first in the app."
        label="Featured"
        onChange={set('is_featured')}
      />
      <ListEditor
        disabled={readOnly}
        hint="Sub-topics for moderated caucuses; the AI uses them to shape questions."
        items={form.caucus_topics}
        label="Moderated caucus topics"
        onChange={set('caucus_topics')}
      />
      <ListEditor
        disabled={readOnly}
        hint="Resolutions, treaties and documents students should know."
        items={form.key_frameworks}
        label="Key frameworks"
        onChange={set('key_frameworks')}
      />
    </Drawer>
  );
}

function ImportDrawer({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportResult | null>(null);

  const check = useMutation({
    mutationFn: (chosen: File) => upload<ImportResult>('/imports', chosen),
    onSuccess: setPreview,
  });
  const apply = useMutation({
    mutationFn: (chosen: File) => upload<ImportResult>('/imports?apply=true', chosen),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['agendas'] });
      await queryClient.invalidateQueries({ queryKey: COMMITTEES_KEY });
      toast(`Imported: ${result.agendas.created} new agendas, ${result.agendas.updated} updated.`);
      onClose();
    },
  });

  const nothingToDo =
    preview && !preview.committees.created && !preview.committees.updated && !preview.agendas.created && !preview.agendas.updated;

  return (
    <Drawer
      footer={
        <>
          <span className="spacer" />
          <button className="btn btn-ghost" onClick={onClose} type="button">
            Cancel
          </button>
          <button
            className="btn btn-primary"
            disabled={!file || !preview || !!nothingToDo || apply.isPending}
            onClick={() => file && apply.mutate(file)}
            type="button"
          >
            {apply.isPending ? 'Importing…' : 'Import'}
          </button>
        </>
      }
      onClose={onClose}
      subtitle="The SYM catalogue (.json) or the agenda list (.csv). Re-importing keeps your status and order choices."
      title="Import committees and agendas"
    >
      <ErrorNote error={check.error ?? apply.error} />
      <label className="upload">
        <strong>Choose a file</strong>
        <span className="muted">.json catalogue or .csv agenda list, up to 5 MB</span>
        <input
          accept=".json,.csv"
          onChange={(event) => {
            const chosen = event.target.files?.[0] ?? null;
            setFile(chosen);
            setPreview(null);
            if (chosen) check.mutate(chosen);
          }}
          type="file"
        />
      </label>
      {check.isPending ? <p className="muted">Checking the file…</p> : null}
      {preview ? (
        <>
          <h3>Preview — nothing has changed yet</h3>
          <div className="field-label">Committees</div>
          <Counts counts={preview.committees} />
          <div className="field-label">Agendas</div>
          <Counts counts={preview.agendas} />
          {preview.problems.length ? (
            <div className="alert alert-error">
              {preview.problems.map((problem) => (
                <div key={problem}>{problem}</div>
              ))}
            </div>
          ) : null}
          {nothingToDo ? <div className="alert alert-success">Everything in this file is already up to date.</div> : null}
          <p className="muted">New agendas with SYM-written caucus topics or frameworks arrive “In review”; others are published.</p>
        </>
      ) : null}
    </Drawer>
  );
}

function Counts({ counts }: { counts: ImportResult['agendas'] }) {
  return (
    <div className="counts">
      <div>
        <b>{counts.created}</b>new
      </div>
      <div>
        <b>{counts.updated}</b>updated
      </div>
      <div>
        <b>{counts.unchanged}</b>unchanged
      </div>
    </div>
  );
}
