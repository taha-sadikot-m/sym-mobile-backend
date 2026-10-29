import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router';
import { RequireAdmin } from './auth';
import { Shell } from './components/Shell';
import { Activity } from './pages/Activity';
import { Agendas } from './pages/Agendas';
import { Committees } from './pages/Committees';
import { Countries } from './pages/Countries';
import { LiveDebates } from './pages/Live';
import { Moderation } from './pages/Moderation';
import { Dashboard } from './pages/Dashboard';
import { Seats } from './pages/Seats';
import { Settings } from './pages/Settings';
import { Students } from './pages/Students';
import { Ai } from './pages/Ai';
import { Notifications } from './pages/Notifications';
import { SignIn } from './pages/SignIn';
import { Topics } from './pages/Topics';
import { ToastProvider } from './ui';
import './styles.css';
import './content.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

const router = createBrowserRouter(
  [
    { path: '/sign-in', element: <SignIn /> },
    {
      element: (
        <RequireAdmin>
          <Shell />
        </RequireAdmin>
      ),
      children: [
        { index: true, element: <Dashboard /> },
        { path: 'committees', element: <Committees /> },
        { path: 'agendas', element: <Agendas /> },
        { path: 'countries', element: <Countries /> },
        { path: 'seats', element: <Seats /> },
        { path: 'topics', element: <Topics /> },
        { path: 'settings', element: <Settings /> },
        { path: 'students', element: <Students /> },
        { path: 'live', element: <LiveDebates /> },
        { path: 'moderation', element: <Moderation /> },
        { path: 'notifications', element: <Notifications /> },
        { path: 'ai', element: <Ai /> },
        { path: 'activity', element: <Activity /> },
        { path: '*', element: <Navigate replace to="/" /> },
      ],
    },
  ],
  { basename: '/admin' },
);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
