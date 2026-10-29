import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type Setting } from '../api';
import { ErrorNote, NumberField, PageHead, TextField, Toggle, formatDate, useCan, useToast } from '../ui';

const SETTINGS_KEY = ['settings'] as const;

/** Labels and units for each field; anything not listed falls back to its key. */
const FIELDS: Record<string, { label: string; suffix?: string; hint?: string }> = {
  target_seconds: { label: 'Target time', suffix: 'sec', hint: 'What the Chair announces.' },
  warn_seconds_left: { label: 'Warning before target', suffix: 'sec' },
  hard_stop_seconds: { label: 'Hard stop', suffix: 'sec', hint: 'Recording stops here.' },
  count: { label: 'Questions per session' },
  answer_seconds: { label: 'Answer time', suffix: 'sec' },
  follow_up_seconds: { label: 'Follow-up time', suffix: 'sec', hint: '0 turns follow-ups off.' },
  conclude_warning_seconds: { label: '“Please conclude” at', suffix: 'sec left' },
  floor_cue_seconds: { label: 'Pause before mic opens', suffix: 'sec' },
  allow_repeat: { label: 'Allow “Ask to repeat” once' },
  grace_seconds: { label: 'Wrap-up grace', suffix: 'sec' },
  amber_seconds: { label: 'Timer turns amber at', suffix: 'sec left' },
  red_seconds: { label: 'Timer turns red at', suffix: 'sec left' },
  crossex_answer_seconds: { label: 'Cross-ex answer window', suffix: 'sec' },
  timeout_seconds: { label: 'Prep time-out', suffix: 'sec', hint: 'One each. 0 turns it off.' },
  lobby_open_minutes: { label: 'Lobby opens before start', suffix: 'min' },
  no_show_grace_minutes: { label: 'No-show grace', suffix: 'min' },
  disconnect_pause_seconds: { label: 'Pause on disconnect', suffix: 'sec' },
  challenge_expiry_hours: { label: 'Challenges expire after', suffix: 'hours' },
  mun_single: { label: 'MUN — single player' },
  mun_multi: { label: 'MUN — multi-delegate' },
  debate_ai: { label: 'Debate vs AI' },
  debate_live: { label: 'Live 1:1 debates' },
  min_supported: { label: 'Minimum supported version', hint: 'Older apps must update.' },
  latest: { label: 'Latest version', hint: 'Older apps see an optional update.' },
  update_message: { label: 'Update message' },
  gemini_input_per_million: { label: 'Gemini input', suffix: 'USD / 1M tokens' },
  gemini_output_per_million: { label: 'Gemini output', suffix: 'USD / 1M tokens' },
  deepgram_per_minute: { label: 'Deepgram', suffix: 'USD / minute' },
  daily_budget_usd: { label: 'Daily AI budget', suffix: 'USD', hint: 'The dashboard warns above this.' },
};

const DESCRIPTIONS: Record<string, string> = {
  speech: 'The delegate’s opening speech in a MUN session.',
  pois: 'Points of Information on the committee floor.',
  live_formats: 'Turn order and lengths for live 1:1 debates. Both sides must get equal time.',
  live_rules: 'Timers, lobby and fairness rules for live 1:1 debates.',
  features: 'Switch whole features on or off in the app without a new release.',
  app_versions: 'Ask students to update the app.',
  ai_costs: 'Only used to estimate spend on the dashboard. Check your providers’ current prices.',
};

export function Settings() {
  const settings = useQuery({ queryKey: SETTINGS_KEY, queryFn: () => api<Setting[]>('/settings') });
  const can = useCan();
  return (
    <>
      <PageHead
        subtitle={
          can.editSettings
            ? 'Changes reach the app within a minute. Every change is logged.'
            : 'Only super admins can change these.'
        }
        title="Rules & settings"
      />
      <ErrorNote error={settings.error} />
      <div className="settings">
        {settings.data?.map((setting) => <SettingCard key={`${setting.key}-${setting.updated_at}`} setting={setting} />)}
      </div>
    </>
  );
}

function SettingCard({ setting }: { setting: Setting }) {
  const can = useCan();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [value, setValue] = useState<Record<string, unknown>>(setting.value);
  const dirty = JSON.stringify(value) !== JSON.stringify(setting.value);
  const readOnly = !can.editSettings;

  const refresh = (saved: Setting) =>
    queryClient.setQueryData<Setting[]>(SETTINGS_KEY, (current) => current?.map((item) => (item.key === saved.key ? saved : item)));

  const save = useMutation({
    mutationFn: () => api<Setting>(`/settings/${setting.key}`, { method: 'PUT', body: { value } }),
    onSuccess: (saved) => {
      refresh(saved);
      toast(`${setting.label} saved.`);
    },
  });
  const reset = useMutation({
    mutationFn: () => api<Setting>(`/settings/${setting.key}/reset`, { method: 'POST' }),
    onSuccess: (saved) => {
      refresh(saved);
      toast(`${setting.label} reset to defaults.`);
    },
  });

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>{setting.label}</h2>
          <p className="muted">{DESCRIPTIONS[setting.key]}</p>
        </div>
        <span className="muted">
          {setting.is_default ? 'Defaults' : `Changed ${formatDate(setting.updated_at)}${setting.updated_by ? ` by ${setting.updated_by}` : ''}`}
        </span>
      </div>
      <ErrorNote error={save.error ?? reset.error} />
      {setting.key === 'live_formats' ? (
        <FormatsEditor disabled={readOnly} onChange={setValue} value={value as unknown as FormatsValue} />
      ) : (
        <div className="settings-grid">
          {Object.entries(value).map(([key, field]) => (
            <SimpleField disabled={readOnly} key={key} name={key} onChange={(next) => setValue({ ...value, [key]: next })} value={field} />
          ))}
        </div>
      )}
      {!readOnly ? (
        <div className="card-foot">
          {!setting.is_default ? (
            <button
              className="btn btn-ghost"
              disabled={reset.isPending}
              onClick={() => window.confirm(`Reset ${setting.label} to the defaults?`) && reset.mutate()}
              type="button"
            >
              Reset to defaults
            </button>
          ) : null}
          {dirty ? (
            <button className="btn btn-ghost" onClick={() => setValue(setting.value)} type="button">
              Discard
            </button>
          ) : null}
          <button className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()} type="button">
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function SimpleField({ name, value, onChange, disabled }: { name: string; value: unknown; onChange: (value: unknown) => void; disabled: boolean }) {
  const meta = FIELDS[name] ?? { label: name.replace(/_/g, ' ') };
  if (typeof value === 'boolean') {
    return <Toggle checked={value} disabled={disabled} hint={meta.hint} label={meta.label} onChange={onChange} />;
  }
  if (typeof value === 'number') {
    return (
      <NumberField
        disabled={disabled}
        hint={meta.hint}
        label={meta.label}
        onChange={(next) => onChange(next ?? 0)}
        step={Number.isInteger(value) ? 1 : 0.0001}
        suffix={meta.suffix}
        value={value}
      />
    );
  }
  return <TextField disabled={disabled} hint={meta.hint} label={meta.label} onChange={onChange} value={String(value ?? '')} />;
}

type Turn = { phase: string; side: 'for' | 'against'; seconds: number };
type Format = { id: string; name: string; summary: string; best_for: string; enabled: boolean; turns: Turn[] };
type FormatsValue = { formats: Format[] };

const PHASES = [
  ['opening', 'Opening'],
  ['crossex', 'Cross-examination'],
  ['rebuttal', 'Rebuttal'],
  ['rapid', 'Rapid turn'],
  ['closing', 'Closing'],
] as const;

function minutes(turns: Turn[], side?: Turn['side']) {
  const seconds = turns.filter((turn) => !side || turn.side === side).reduce((sum, turn) => sum + turn.seconds, 0);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function FormatsEditor({ value, onChange, disabled }: { value: FormatsValue; onChange: (value: Record<string, unknown>) => void; disabled: boolean }) {
  const update = (index: number, next: Partial<Format>) =>
    onChange({ formats: value.formats.map((format, at) => (at === index ? { ...format, ...next } : format)) });

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {value.formats.map((format, index) => {
        const fair = minutes(format.turns, 'for') === minutes(format.turns, 'against');
        const setTurn = (at: number, next: Partial<Turn>) =>
          update(index, { turns: format.turns.map((turn, position) => (position === at ? { ...turn, ...next } : turn)) });
        return (
          <div className="format" key={format.id}>
            <div className="row-2">
              <TextField disabled={disabled} label="Name" maxLength={40} onChange={(name) => update(index, { name })} value={format.name} />
              <TextField disabled={disabled} label="Best for" maxLength={80} onChange={(best_for) => update(index, { best_for })} value={format.best_for} />
            </div>
            <TextField disabled={disabled} label="Summary" maxLength={160} onChange={(summary) => update(index, { summary })} value={format.summary} />
            <Toggle checked={format.enabled} disabled={disabled} label="Offered in the app" onChange={(enabled) => update(index, { enabled })} />
            <div className="table-wrap">
              <table className="turns">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Phase</th>
                    <th>Side</th>
                    <th>Seconds</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {format.turns.map((turn, at) => (
                    <tr key={at}>
                      <td className="muted">{at + 1}</td>
                      <td>
                        <select aria-label={`Turn ${at + 1} phase`} disabled={disabled} onChange={(event) => setTurn(at, { phase: event.target.value })} value={turn.phase}>
                          {PHASES.map(([id, label]) => (
                            <option key={id} value={id}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <select
                          aria-label={`Turn ${at + 1} side`}
                          disabled={disabled}
                          onChange={(event) => setTurn(at, { side: event.target.value as Turn['side'] })}
                          value={turn.side}
                        >
                          <option value="for">For</option>
                          <option value="against">Against</option>
                        </select>
                      </td>
                      <td>
                        <input
                          aria-label={`Turn ${at + 1} seconds`}
                          disabled={disabled}
                          max={600}
                          min={15}
                          onChange={(event) => setTurn(at, { seconds: Number(event.target.value) })}
                          step={15}
                          type="number"
                          value={turn.seconds}
                        />
                      </td>
                      <td>
                        {!disabled && format.turns.length > 2 ? (
                          <button
                            aria-label={`Remove turn ${at + 1}`}
                            className="icon-btn"
                            onClick={() => update(index, { turns: format.turns.filter((_, position) => position !== at) })}
                            type="button"
                          >
                            ×
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar">
              {!disabled ? (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() =>
                    update(index, {
                      turns: [...format.turns, { phase: 'rebuttal', side: format.turns.at(-1)?.side === 'for' ? 'against' : 'for', seconds: 60 }],
                    })
                  }
                  type="button"
                >
                  Add turn
                </button>
              ) : null}
              <span className={fair ? 'muted' : 'status status-in_review'}>
                Total {minutes(format.turns)} · For {minutes(format.turns, 'for')} · Against {minutes(format.turns, 'against')}
                {fair ? '' : ' — sides must be equal'}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
