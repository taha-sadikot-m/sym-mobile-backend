export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Calls the admin API. The session cookie is sent automatically; the header blocks cross-site requests. */
export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await fetch(`/admin/api${path}`, {
    method: init.method ?? 'GET',
    credentials: 'same-origin',
    headers: {
      'X-SYM-Admin': '1',
      ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(data));
  }
  return data as T;
}

/** FastAPI sends either a sentence or, for invalid input, a list of field errors. */
function errorMessage(data: unknown): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const field = first.loc?.[first.loc.length - 1];
    const message = (first.msg ?? 'is invalid').replace(/^Value error, /, '');
    return typeof field === 'string' ? `${field[0]?.toUpperCase()}${field.slice(1)}: ${message}` : message;
  }
  return 'Something went wrong. Try again.';
}

/** Upload a file as multipart form data. */
export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`/admin/api${path}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-SYM-Admin': '1' },
    body: form,
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, errorMessage(data));
  return data as T;
}

/** Build a query string, skipping empty values. */
export function query(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

export type AdminRole = 'super_admin' | 'content_manager' | 'moderator' | 'analyst';

export type Admin = {
  id: string;
  email: string;
  full_name: string;
  role: AdminRole;
};

export type PasswordStep = {
  next: 'totp' | 'totp_setup';
  challenge: string;
  secret: string | null;
  qr_svg: string | null;
};

export type AuditEntry = {
  id: string;
  action: string;
  admin_email: string | null;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  ip: string | null;
  created_at: string;
};

export type SystemStatus = {
  version: string;
  environment: string;
  web_tables_ok: boolean;
  web_table_changes: string[];
  active_admins: number;
  content: {
    committees_published: number;
    committees_total: number;
    agendas_published: number;
    agendas_in_review: number;
    agendas_total: number;
    countries_active: number;
    topics_published: number;
  };
  recent_activity: AuditEntry[];
};

export const ROLE_LABELS: Record<AdminRole, string> = {
  super_admin: 'Super admin',
  content_manager: 'Content manager',
  moderator: 'Moderator',
  analyst: 'Analyst',
};

export type Status = 'draft' | 'in_review' | 'published' | 'archived';
export type SeatType = 'country' | 'portfolio' | 'journalist' | 'court_role';
export type Difficulty = 'Beginner' | 'Intermediate' | 'Advanced';
export type SeatRule = 'all_un_members' | 'selected';

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type Committee = {
  id: string;
  slug: string;
  abbr: string;
  name: string;
  category: string;
  seat_type: SeatType;
  difficulty: Difficulty;
  description: string;
  decision_rule: string;
  binding: string;
  typical_seats: number | null;
  themes: { theme: string; share_pct: number | null }[];
  status: Status;
  seat_rule: SeatRule;
  sort_order: number;
  agenda_count: number;
  published_agenda_count: number;
  seat_count: number;
  updated_at: string;
};

export type Agenda = {
  id: string;
  committee_id: string;
  committee_abbr: string;
  slug: string;
  title: string;
  theme: string | null;
  seen_at: string | null;
  kind: string;
  is_featured: boolean;
  caucus_topics: string[];
  key_frameworks: string[];
  status: Status;
  sort_order: number;
  updated_at: string;
};

export type Country = {
  code: string;
  name: string;
  region: string;
  is_un_member: boolean;
  groupings: string[];
  summary: string;
  is_active: boolean;
  seat_count: number;
  updated_at: string;
};

export type Seats = { seat_rule: SeatRule; country_codes: string[] };

export type TopicCategory = 'education' | 'technology' | 'environment' | 'society' | 'politics' | 'economy';

export type Topic = {
  id: string;
  slug: string;
  title: string;
  category: TopicCategory;
  context: string;
  for_points: string[];
  against_points: string[];
  status: Status;
  sort_order: number;
  updated_at: string;
};

export type Setting = {
  key: string;
  label: string;
  value: Record<string, unknown>;
  defaults: Record<string, unknown>;
  is_default: boolean;
  updated_at: string | null;
  updated_by: string | null;
};

export type ImportResult = {
  applied: boolean;
  committees: { created: number; updated: number; unchanged: number };
  agendas: { created: number; updated: number; unchanged: number };
  problems: string[];
};

export const SEAT_TYPE_LABELS: Record<SeatType, string> = {
  country: 'Country delegates',
  portfolio: 'Portfolios (crisis / parliament)',
  journalist: 'Journalists',
  court_role: 'Court roles',
};

export const TOPIC_CATEGORY_LABELS: Record<TopicCategory, string> = {
  education: 'Education',
  technology: 'Technology',
  environment: 'Environment',
  society: 'Society',
  politics: 'Politics',
  economy: 'Economy',
};

export type Insights = {
  practice: {
    sessions_total: number;
    sessions_this_week: number;
    students_practising_this_week: number;
    average_score: number | null;
    ai_feedback_share: number;
    debates_this_week: number;
    top_committees: { committee: string; sessions: number; average_score: number }[];
  };
  ai: {
    calls_today: number;
    failures_today: number;
    cost_today_usd: number;
    cost_week_usd: number;
    daily_budget_usd: number;
    over_budget: boolean;
    by_feature: { feature: string; calls: number; failures: number; average_ms: number }[];
  };
};
