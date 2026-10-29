import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { api, type Country } from '../api';
import { Drawer, Empty, ErrorNote, PageHead, SearchInput, TextArea, TextField, Toggle, useCan, useToast } from '../ui';

export const COUNTRIES_KEY = ['countries'] as const;

export function useCountries() {
  return useQuery({ queryKey: COUNTRIES_KEY, queryFn: () => api<Country[]>('/countries'), staleTime: 60_000 });
}

export const GROUPINGS = ['P5', 'G7', 'G20', 'BRICS', 'EU', 'NATO', 'AU', 'Arab League', 'ASEAN'];

export function Countries() {
  const countries = useCountries();
  const can = useCan();
  const [q, setQ] = useState('');
  const [region, setRegion] = useState('');
  const [grouping, setGrouping] = useState('');
  const [editing, setEditing] = useState<Country | 'new' | null>(null);

  const regions = useMemo(() => [...new Set((countries.data ?? []).map((item) => item.region))].sort(), [countries.data]);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (countries.data ?? []).filter(
      (item) =>
        (!region || item.region === region) &&
        (!grouping || item.groupings.includes(grouping)) &&
        (!needle || `${item.name} ${item.code}`.toLowerCase().includes(needle)),
    );
  }, [countries.data, q, region, grouping]);

  return (
    <>
      <PageHead
        actions={
          can.editContent ? (
            <button className="btn btn-primary" onClick={() => setEditing('new')} type="button">
              Add country
            </button>
          ) : null
        }
        subtitle="Countries students can represent. The summary is the short brief shown when a country is assigned."
        title="Countries"
      />
      <div className="toolbar">
        <SearchInput onChange={setQ} placeholder="Search by name or code" value={q} />
        <select aria-label="Region" onChange={(event) => setRegion(event.target.value)} value={region}>
          <option value="">All regional groups</option>
          {regions.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
        <select aria-label="Grouping" onChange={(event) => setGrouping(event.target.value)} value={grouping}>
          <option value="">All groupings</option>
          {GROUPINGS.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      </div>
      <ErrorNote error={countries.error} />
      <div className="card table-card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Country</th>
                <th className="hide-sm">Regional group</th>
                <th className="hide-sm">Groupings</th>
                <th className="num">Seats</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr className="clickable" key={item.code} onClick={() => setEditing(item)}>
                  <td>
                    <div className="title">
                      {item.name} <span className="mono">{item.code.toUpperCase()}</span>
                    </div>
                    <div className="sub">
                      {!item.is_active ? 'Hidden from the app · ' : ''}
                      {!item.is_un_member ? 'Observer state · ' : ''}
                      {item.summary || 'No summary yet'}
                    </div>
                  </td>
                  <td className="hide-sm">{item.region}</td>
                  <td className="hide-sm">
                    {item.groupings.map((group) => (
                      <span className="tag" key={group}>
                        {group}
                      </span>
                    ))}
                  </td>
                  <td className="num">{item.seat_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {countries.isPending ? <Empty>Loading…</Empty> : !rows.length ? <Empty>No countries match.</Empty> : null}
      </div>
      {editing ? (
        <CountryDrawer country={editing === 'new' ? null : editing} onClose={() => setEditing(null)} regions={regions} />
      ) : null}
    </>
  );
}

type Form = Omit<Country, 'seat_count' | 'updated_at'>;

function CountryDrawer({ country, regions, onClose }: { country: Country | null; regions: string[]; onClose: () => void }) {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const readOnly = !can.editContent;
  const [form, setForm] = useState<Form>(
    country ?? { code: '', name: '', region: regions[0] ?? '', is_un_member: true, groupings: [], summary: '', is_active: true },
  );
  const set = <K extends keyof Form>(key: K) => (value: Form[K]) => setForm((current) => ({ ...current, [key]: value }));

  const save = useMutation({
    mutationFn: () => {
      const { code, ...rest } = form;
      return country
        ? api<Country>(`/countries/${country.code}`, { method: 'PATCH', body: rest })
        : api<Country>('/countries', { method: 'POST', body: { code, ...rest } });
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: COUNTRIES_KEY });
      toast(`Saved ${saved.name}.`);
      onClose();
    },
  });

  return (
    <Drawer
      footer={
        readOnly ? (
          <span className="muted">Your role can view countries but not change them.</span>
        ) : (
          <>
            <span className="spacer" />
            <button className="btn btn-ghost" onClick={onClose} type="button">
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={save.isPending || form.name.trim().length < 2 || (!country && form.code.length !== 2)}
              onClick={() => save.mutate()}
              type="button"
            >
              {save.isPending ? 'Saving…' : country ? 'Save changes' : 'Add country'}
            </button>
          </>
        )
      }
      onClose={onClose}
      subtitle={country ? `Code ${country.code.toUpperCase()} · seated on ${country.seat_count} limited committees` : undefined}
      title={country ? country.name : 'Add country'}
    >
      <ErrorNote error={save.error} />
      <div className="row-2">
        <TextField disabled={readOnly} label="Name" maxLength={120} onChange={set('name')} value={form.name} />
        <TextField
          disabled={readOnly || !!country}
          hint="ISO two-letter code"
          label="Code"
          maxLength={2}
          onChange={(value) => set('code')(value.toLowerCase())}
          value={form.code.toUpperCase()}
        />
      </div>
      <div className="field">
        <label htmlFor="region">Regional group</label>
        <input disabled={readOnly} id="region" list="regions" onChange={(event) => set('region')(event.target.value)} value={form.region} />
        <datalist id="regions">
          {regions.map((item) => (
            <option key={item} value={item} />
          ))}
        </datalist>
      </div>
      <div className="field">
        <span className="field-label">Groupings</span>
        <div className="chips">
          {GROUPINGS.map((group) => {
            const on = form.groupings.includes(group);
            return (
              <button
                aria-pressed={on}
                className={`chip ${on ? 'chip-on' : ''}`}
                disabled={readOnly}
                key={group}
                onClick={() => set('groupings')(on ? form.groupings.filter((item) => item !== group) : [...form.groupings, group])}
                type="button"
              >
                {group}
              </button>
            );
          })}
        </div>
      </div>
      <TextArea
        disabled={readOnly}
        hint="One sentence on the country’s usual stance; shown to the student and used by the AI opponents."
        label="Summary"
        maxLength={500}
        onChange={set('summary')}
        value={form.summary}
      />
      <Toggle checked={form.is_un_member} disabled={readOnly} label="UN member state" hint="Off for observer states." onChange={set('is_un_member')} />
      <Toggle checked={form.is_active} disabled={readOnly} label="Available in the app" onChange={set('is_active')} />
    </Drawer>
  );
}
