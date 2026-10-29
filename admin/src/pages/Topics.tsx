import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { api, TOPIC_CATEGORY_LABELS, type Status, type Topic, type TopicCategory } from '../api';
import {
  Chips,
  Drawer,
  Empty,
  ErrorNote,
  ListEditor,
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

const TOPICS_KEY = ['topics'] as const;
type CategoryFilter = 'all' | TopicCategory;

export function Topics() {
  const can = useCan();
  const topics = useQuery({ queryKey: TOPICS_KEY, queryFn: () => api<Topic[]>('/topics') });
  const [q, setQ] = useState('');
  const [category, setCategory] = useState<CategoryFilter>('all');
  const [editing, setEditing] = useState<Topic | 'new' | null>(null);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (topics.data ?? []).filter(
      (item) =>
        (category === 'all' || item.category === category) &&
        (!needle || `${item.title} ${item.context}`.toLowerCase().includes(needle)),
    );
  }, [topics.data, q, category]);

  return (
    <>
      <PageHead
        actions={
          can.editContent ? (
            <button className="btn btn-primary" onClick={() => setEditing('new')} type="button">
              New topic
            </button>
          ) : null
        }
        subtitle="Motions for 1:1 debates. The For and Against points are what the AI opponent argues from."
        title="Debate topics"
      />
      <div className="toolbar">
        <SearchInput onChange={setQ} placeholder="Search topics" value={q} />
      </div>
      <Chips<CategoryFilter>
        label="Filter by category"
        onChange={setCategory}
        options={[
          { value: 'all', label: 'All', count: topics.data?.length },
          ...Object.entries(TOPIC_CATEGORY_LABELS).map(([value, label]) => ({
            value: value as TopicCategory,
            label,
            count: topics.data?.filter((item) => item.category === value).length,
          })),
        ]}
        value={category}
      />
      <ErrorNote error={topics.error} />
      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Motion</th>
                <th className="hide-sm">Category</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr className="clickable" key={item.id} onClick={() => setEditing(item)}>
                  <td>
                    <div className="title">{item.title}</div>
                    <div className="sub">{item.context}</div>
                  </td>
                  <td className="hide-sm">{TOPIC_CATEGORY_LABELS[item.category]}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {topics.isPending ? <Empty>Loading…</Empty> : !rows.length ? <Empty>No topics match.</Empty> : null}
      </div>
      {editing ? <TopicDrawer onClose={() => setEditing(null)} topic={editing === 'new' ? null : editing} /> : null}
    </>
  );
}

type Form = Pick<Topic, 'title' | 'category' | 'context' | 'for_points' | 'against_points'> & { status: Status };

function TopicDrawer({ topic, onClose }: { topic: Topic | null; onClose: () => void }) {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const readOnly = !can.editContent;
  const [form, setForm] = useState<Form>(
    topic ?? { title: '', category: 'education', context: '', for_points: [], against_points: [], status: 'draft' },
  );
  const set = <K extends keyof Form>(key: K) => (value: Form[K]) => setForm((current) => ({ ...current, [key]: value }));
  const done = async (message: string) => {
    await queryClient.invalidateQueries({ queryKey: TOPICS_KEY });
    toast(message);
    onClose();
  };
  const body = {
    ...form,
    for_points: form.for_points.map((item) => item.trim()).filter(Boolean),
    against_points: form.against_points.map((item) => item.trim()).filter(Boolean),
  };
  const save = useMutation({
    mutationFn: () => (topic ? api<Topic>(`/topics/${topic.id}`, { method: 'PATCH', body }) : api<Topic>('/topics', { method: 'POST', body })),
    onSuccess: () => done(topic ? 'Topic saved.' : 'Topic created.'),
  });
  const remove = useMutation({
    mutationFn: () => api(`/topics/${topic?.id}`, { method: 'DELETE' }),
    onSuccess: () => done('Topic deleted.'),
  });
  const notReady = form.status === 'published' && (body.for_points.length < 2 || body.against_points.length < 2);

  return (
    <Drawer
      footer={
        readOnly ? (
          <span className="muted">Your role can view topics but not change them.</span>
        ) : (
          <>
            {topic && topic.status !== 'published' ? (
              <button
                className="btn btn-danger"
                disabled={remove.isPending}
                onClick={() => window.confirm('Delete this topic?') && remove.mutate()}
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
              disabled={save.isPending || form.title.trim().length < 8 || notReady}
              onClick={() => save.mutate()}
              type="button"
            >
              {save.isPending ? 'Saving…' : topic ? 'Save changes' : 'Create topic'}
            </button>
          </>
        )
      }
      onClose={onClose}
      title={topic ? 'Edit topic' : 'New topic'}
    >
      <ErrorNote error={save.error ?? remove.error} />
      <TextField disabled={readOnly} label="Motion" maxLength={300} onChange={set('title')} value={form.title} />
      <div className="row-2">
        <SelectField
          disabled={readOnly}
          label="Category"
          onChange={(value) => set('category')(value as TopicCategory)}
          options={Object.entries(TOPIC_CATEGORY_LABELS).map(([value, label]) => ({ value, label }))}
          value={form.category}
        />
        <StatusSelect disabled={readOnly} onChange={set('status')} value={form.status} />
      </div>
      <TextArea disabled={readOnly} label="Context" maxLength={400} onChange={set('context')} rows={2} value={form.context} />
      <ListEditor disabled={readOnly} items={form.for_points} label="For points" max={6} onChange={set('for_points')} />
      <ListEditor disabled={readOnly} items={form.against_points} label="Against points" max={6} onChange={set('against_points')} />
      {notReady ? <div className="alert alert-error">Add at least two For and two Against points before publishing.</div> : null}
    </Drawer>
  );
}
