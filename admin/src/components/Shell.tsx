import { useQueryClient } from '@tanstack/react-query';
import { NavLink, Outlet, useNavigate } from 'react-router';
import { api, ROLE_LABELS, type AdminRole } from '../api';
import { useMe } from '../auth';

type NavItem = { label: string; to: string; roles?: AdminRole[] };

const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Overview', items: [{ label: 'Dashboard', to: '/' }] },
  {
    group: 'Content',
    items: [
      { label: 'Committees', to: '/committees' },
      { label: 'Agendas', to: '/agendas' },
      { label: 'Countries', to: '/countries' },
      { label: 'Committee seats', to: '/seats' },
      { label: 'Debate topics', to: '/topics' },
      { label: 'Rules & settings', to: '/settings' },
    ],
  },
  {
    group: 'People',
    items: [
      { label: 'Students', to: '/students', roles: ['super_admin', 'moderator'] },
      { label: 'Live debates', to: '/live' },
      { label: 'Moderation', to: '/moderation' },
      { label: 'Notifications', to: '/notifications' },
    ],
  },
  {
    group: 'System',
    items: [
      { label: 'AI & prompts', to: '/ai' },
      { label: 'Activity log', to: '/activity' },
    ],
  },
];

export function Shell() {
  const me = useMe();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const signOut = async () => {
    await api('/auth/sign-out', { method: 'POST' }).catch(() => undefined);
    queryClient.clear();
    navigate('/sign-in', { replace: true });
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">S</span> SYM Admin
        </div>
        <nav aria-label="Admin sections" className="nav">
          {NAV.map((section) => (
            <div key={section.group}>
              <div className="nav-label">{section.group}</div>
              {section.items
                .filter((item) => !item.roles || (me.data && item.roles.includes(me.data.role)))
                .map((item) => (
                  <NavLink end key={item.label} to={item.to}>
                    {item.label}
                  </NavLink>
                ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          {me.data ? (
            <div className="who">
              <strong>{me.data.full_name}</strong>
              <span>{ROLE_LABELS[me.data.role]}</span>
            </div>
          ) : null}
          <button className="btn btn-ghost" onClick={() => void signOut()} type="button">
            Sign out
          </button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
