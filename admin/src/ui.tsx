import { createContext, useCallback, useContext, useEffect, useId, useState, type ReactNode } from 'react';
import type { AdminRole } from './api';
import { useMe } from './auth';

/* ---------- Permissions ---------- */

export function useCan() {
  const role: AdminRole | undefined = useMe().data?.role;
  return {
    editContent: role === 'super_admin' || role === 'content_manager',
    editSettings: role === 'super_admin',
    viewStudents: role === 'super_admin' || role === 'moderator',
  };
}

/* ---------- Status ---------- */

export type ContentStatus = 'draft' | 'in_review' | 'published' | 'archived';

export const STATUS_LABELS: Record<ContentStatus, string> = {
  draft: 'Draft',
  in_review: 'In review',
  published: 'Published',
  archived: 'Archived',
};

export function StatusBadge({ status }: { status: ContentStatus }) {
  return <span className={`status status-${status}`}>{STATUS_LABELS[status]}</span>;
}

export function StatusSelect({
  value,
  onChange,
  disabled,
}: {
  value: ContentStatus;
  onChange: (status: ContentStatus) => void;
  disabled?: boolean;
}) {
  return (
    <SelectField
      disabled={disabled}
      label="Status"
      onChange={(next) => onChange(next as ContentStatus)}
      options={Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))}
      value={value}
      hint="Only published content reaches the app."
    />
  );
}

/* ---------- Toasts ---------- */

type Toast = { id: number; tone: 'ok' | 'error'; text: string };
const ToastContext = createContext<(text: string, tone?: Toast['tone']) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast['tone'] = 'ok') => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, tone, text }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4200);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="toasts">
        {toasts.map((toast) => (
          <div className={`toast toast-${toast.tone}`} key={toast.id} role="status">
            {toast.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

/* ---------- Layout pieces ---------- */

export function PageHead({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="page-head page-head-row">
      <div>
        <h1>{title}</h1>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

export function Drawer({
  open = true,
  title,
  subtitle,
  onClose,
  children,
  footer,
}: {
  open?: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="drawer-root">
      <button aria-label="Close panel" className="drawer-backdrop" onClick={onClose} type="button" />
      <aside aria-label={title} aria-modal="true" className="drawer" role="dialog">
        <header className="drawer-head">
          <div>
            <h2>{title}</h2>
            {subtitle ? <p className="muted">{subtitle}</p> : null}
          </div>
          <button aria-label="Close" className="icon-btn" onClick={onClose} type="button">
            ×
          </button>
        </header>
        <div className="drawer-body">{children}</div>
        {footer ? <footer className="drawer-foot">{footer}</footer> : null}
      </aside>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const text = error instanceof Error ? error.message : 'Something went wrong.';
  return (
    <div className="alert alert-error" role="alert">
      {text}
    </div>
  );
}

/* ---------- Fields ---------- */

type Base = { label: string; hint?: string; disabled?: boolean };

export function TextField({
  label,
  hint,
  disabled,
  value,
  onChange,
  required,
  maxLength,
  placeholder,
}: Base & {
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  maxLength?: number;
  placeholder?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        disabled={disabled}
        id={id}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        required={required}
        value={value}
      />
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function TextArea({
  label,
  hint,
  disabled,
  value,
  onChange,
  maxLength,
  rows = 3,
}: Base & { value: string; onChange: (value: string) => void; maxLength?: number; rows?: number }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea
        disabled={disabled}
        id={id}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        value={value}
      />
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function NumberField({
  label,
  hint,
  disabled,
  value,
  onChange,
  min,
  max,
  step,
  suffix,
}: Base & {
  value: number | null;
  onChange: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="with-suffix">
        <input
          disabled={disabled}
          id={id}
          inputMode="numeric"
          max={max}
          min={min}
          onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
          step={step}
          type="number"
          value={value ?? ''}
        />
        {suffix ? <span>{suffix}</span> : null}
      </div>
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function SelectField({
  label,
  hint,
  disabled,
  value,
  onChange,
  options,
}: Base & { value: string; onChange: (value: string) => void; options: { value: string; label: string }[] }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select disabled={disabled} id={id} onChange={(event) => onChange(event.target.value)} value={value}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function Toggle({
  label,
  hint,
  disabled,
  checked,
  onChange,
}: Base & { checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className={`toggle ${disabled ? 'is-disabled' : ''}`}>
      <input checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      <span className="toggle-track" aria-hidden="true" />
      <span>
        <span className="toggle-label">{label}</span>
        {hint ? <span className="hint">{hint}</span> : null}
      </span>
    </label>
  );
}

/** Edit a short list of sentences (caucus topics, frameworks, For/Against points). */
export function ListEditor({
  label,
  hint,
  disabled,
  items,
  onChange,
  max = 12,
  placeholder,
}: Base & { items: string[]; onChange: (items: string[]) => void; max?: number; placeholder?: string }) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const text = draft.trim();
    if (!text || items.length >= max) return;
    onChange([...items, text]);
    setDraft('');
  };
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      {items.length ? (
        <ol className="list-editor">
          {items.map((item, index) => (
            <li key={`${index}-${item}`}>
              <input
                aria-label={`${label} ${index + 1}`}
                disabled={disabled}
                onChange={(event) => onChange(items.map((current, at) => (at === index ? event.target.value : current)))}
                value={item}
              />
              {!disabled ? (
                <button
                  aria-label={`Remove ${label.toLowerCase()} ${index + 1}`}
                  className="icon-btn"
                  onClick={() => onChange(items.filter((_, at) => at !== index))}
                  type="button"
                >
                  ×
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
      {!disabled && items.length < max ? (
        <div className="list-add">
          <input
            aria-label={`Add to ${label.toLowerCase()}`}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                add();
              }
            }}
            placeholder={placeholder ?? 'Type and press Enter'}
            value={draft}
          />
          <button className="btn btn-ghost btn-sm" disabled={!draft.trim()} onClick={add} type="button">
            Add
          </button>
        </div>
      ) : null}
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

/* ---------- Lists ---------- */

export function SearchInput({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return (
    <input
      aria-label={placeholder}
      className="search"
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      type="search"
      value={value}
    />
  );
}

export function Chips<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string; count?: number }[];
  label: string;
}) {
  return (
    <div aria-label={label} className="chips" role="radiogroup">
      {options.map((option) => (
        <button
          aria-checked={value === option.value}
          className={`chip ${value === option.value ? 'chip-on' : ''}`}
          key={option.value}
          onClick={() => onChange(option.value)}
          role="radio"
          type="button"
        >
          {option.label}
          {option.count !== undefined ? <span className="chip-count">{option.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Pager({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="pager">
      <span className="muted">
        {from}–{to} of {total}
      </span>
      <div>
        <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))} type="button">
          Previous
        </button>
        <button className="btn btn-ghost btn-sm" disabled={to >= total} onClick={() => onChange(offset + limit)} type="button">
          Next
        </button>
      </div>
    </div>
  );
}

export function useDebounced<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function formatDate(iso: string | null | undefined) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
