import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { api, type Seats as SeatsData, type SeatRule } from '../api';
import { Empty, ErrorNote, PageHead, SearchInput, useCan, useToast } from '../ui';
import { COMMITTEES_KEY, useCommittees } from './Committees';
import { COUNTRIES_KEY, GROUPINGS, useCountries } from './Countries';

export function Seats() {
  const committees = useCommittees();
  const [params, setParams] = useSearchParams();
  const chosen = params.get('committee') ?? committees.data?.[0]?.id ?? '';
  const committee = committees.data?.find((item) => item.id === chosen);

  return (
    <>
      <PageHead
        subtitle="Which countries can be assigned in each committee. Most bodies are open to every UN member; limited bodies like the Security Council seat specific countries."
        title="Committee seats"
      />
      <ErrorNote error={committees.error} />
      <div className="split">
        <div className="card picker" role="listbox" aria-label="Committees">
          {committees.data?.map((item) => (
            <button
              aria-selected={item.id === chosen}
              className={item.id === chosen ? 'on' : ''}
              key={item.id}
              onClick={() => setParams({ committee: item.id }, { replace: true })}
              role="option"
              type="button"
            >
              <span>{item.abbr}</span>
              <span className="muted">{item.seat_rule === 'selected' ? `${item.seat_count} seats` : 'All members'}</span>
            </button>
          ))}
        </div>
        {committee ? <SeatEditor committeeId={committee.id} key={committee.id} name={`${committee.abbr} — ${committee.name}`} typical={committee.typical_seats} /> : <Empty>Loading…</Empty>}
      </div>
    </>
  );
}

function SeatEditor({ committeeId, name, typical }: { committeeId: string; name: string; typical: number | null }) {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const countries = useCountries();
  const seats = useQuery({ queryKey: ['seats', committeeId], queryFn: () => api<SeatsData>(`/committees/${committeeId}/seats`) });
  const [draft, setDraft] = useState<{ rule: SeatRule; codes: Set<string> } | null>(null);
  const [q, setQ] = useState('');

  const rule = draft?.rule ?? seats.data?.seat_rule ?? 'all_un_members';
  const codes = draft?.codes ?? new Set(seats.data?.country_codes ?? []);
  const dirty = draft !== null;
  const readOnly = !can.editContent;

  const update = (next: Partial<{ rule: SeatRule; codes: Set<string> }>) => setDraft({ rule, codes, ...next });
  const flip = (code: string) => {
    const next = new Set(codes);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    update({ codes: next });
  };
  const addGroup = (group: string) => {
    const next = new Set(codes);
    for (const country of countries.data ?? []) if (country.groupings.includes(group)) next.add(country.code);
    update({ codes: next });
  };

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (countries.data ?? []).filter((item) => item.is_active && (!needle || item.name.toLowerCase().includes(needle)));
  }, [countries.data, q]);

  const save = useMutation({
    mutationFn: () =>
      api<SeatsData>(`/committees/${committeeId}/seats`, {
        method: 'PUT',
        body: { seat_rule: rule, country_codes: [...codes] },
      }),
    onSuccess: async (saved) => {
      queryClient.setQueryData(['seats', committeeId], saved);
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: COMMITTEES_KEY });
      await queryClient.invalidateQueries({ queryKey: COUNTRIES_KEY });
      toast(saved.seat_rule === 'selected' ? `Saved ${saved.country_codes.length} seats.` : 'Open to all UN members.');
    },
  });

  return (
    <div className="card" style={{ display: 'grid', gap: 16 }}>
      <div className="card-head">
        <h2>{name}</h2>
        {typical ? <span className="muted">Typical conference size: {typical} delegates</span> : null}
      </div>
      <ErrorNote error={seats.error ?? save.error} />
      <div className="chips" role="radiogroup" aria-label="Seat rule">
        {(
          [
            ['all_un_members', 'Open to all UN members'],
            ['selected', 'Only selected countries'],
          ] as const
        ).map(([value, label]) => (
          <button
            aria-checked={rule === value}
            className={`chip ${rule === value ? 'chip-on' : ''}`}
            disabled={readOnly}
            key={value}
            onClick={() => update({ rule: value })}
            role="radio"
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      {rule === 'selected' ? (
        <>
          <div className="toolbar">
            <SearchInput onChange={setQ} placeholder="Find a country" value={q} />
            <span className="muted">{codes.size} selected</span>
          </div>
          {!readOnly ? (
            <div className="chips" aria-label="Add a whole group">
              {GROUPINGS.map((group) => (
                <button className="chip" key={group} onClick={() => addGroup(group)} type="button">
                  + {group}
                </button>
              ))}
              <button className="chip" onClick={() => update({ codes: new Set() })} type="button">
                Clear all
              </button>
            </div>
          ) : null}
          <div className="seat-grid">
            {visible.map((country) => (
              <label className="seat" key={country.code}>
                <input checked={codes.has(country.code)} disabled={readOnly} onChange={() => flip(country.code)} type="checkbox" />
                <span>{country.name}</span>
                <span className="code">{country.code.toUpperCase()}</span>
              </label>
            ))}
          </div>
        </>
      ) : (
        <p className="muted">Any active UN member state can be assigned, randomly or by the student’s choice.</p>
      )}

      {!readOnly ? (
        <div className="card-foot">
          {dirty ? (
            <button className="btn btn-ghost" onClick={() => setDraft(null)} type="button">
              Discard changes
            </button>
          ) : null}
          <button className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()} type="button">
            {save.isPending ? 'Saving…' : 'Save seats'}
          </button>
        </div>
      ) : null}
    </div>
  );
}
