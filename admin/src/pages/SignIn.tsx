import { useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { api, ApiError, type Admin, type PasswordStep } from '../api';
import { ME_KEY } from '../auth';

type Stage = { kind: 'password' } | { kind: 'code'; step: PasswordStep };

export function SignIn() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [stage, setStage] = useState<Stage>({ kind: 'password' });
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? '/';

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : 'Couldn’t reach the server. Try again.');
    } finally {
      setBusy(false);
    }
  };

  const submitPassword = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const step = await api<PasswordStep>('/auth/password', { method: 'POST', body: { email, password } });
      setPassword('');
      setStage({ kind: 'code', step });
    });
  };

  const submitCode = (event: FormEvent) => {
    event.preventDefault();
    if (stage.kind !== 'code') return;
    void run(async () => {
      const admin = await api<Admin>('/auth/code', {
        method: 'POST',
        body: { challenge: stage.step.challenge, code: code.replace(/\s/g, '') },
      });
      queryClient.setQueryData(ME_KEY, admin);
      navigate(from === '/sign-in' ? '/' : from, { replace: true });
    });
  };

  const restart = () => {
    setStage({ kind: 'password' });
    setCode('');
    setError('');
  };

  return (
    <div className="auth">
      <section className="auth-hero">
        <div className="brand">
          <span className="brand-mark">S</span> Speak Your Mind
        </div>
        <div>
          <h1>Admin console</h1>
          <p>Manage committees, agendas, countries and students, and keep an eye on how the app is doing.</p>
        </div>
        <p>Restricted to SYM staff. Every sign-in and change is logged.</p>
      </section>

      <section className="auth-panel">
        {stage.kind === 'password' ? (
          <form className="auth-card" onSubmit={submitPassword}>
            <div>
              <h2>Sign in</h2>
              <p className="sub">Use your admin email and password.</p>
            </div>
            {error ? <div className="alert alert-error" role="alert">{error}</div> : null}
            <div className="field">
              <label htmlFor="email">Email</label>
              <input
                autoComplete="username"
                autoFocus
                id="email"
                onChange={(event) => setEmail(event.target.value)}
                required
                type="email"
                value={email}
              />
            </div>
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                autoComplete="current-password"
                id="password"
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </div>
            <button className="btn btn-primary btn-block" disabled={busy} type="submit">
              {busy ? 'Checking…' : 'Continue'}
            </button>
          </form>
        ) : (
          <form className="auth-card" onSubmit={submitCode}>
            <div>
              <h2>{stage.step.next === 'totp_setup' ? 'Set up two-step sign-in' : 'Enter your code'}</h2>
              <p className="sub">
                {stage.step.next === 'totp_setup'
                  ? 'Scan this with Google Authenticator, Microsoft Authenticator or 1Password, then enter the 6-digit code it shows.'
                  : 'Open your authenticator app and enter the 6-digit code for SYM Admin.'}
              </p>
            </div>
            {stage.step.qr_svg ? (
              <div className="qr">
                <img alt="QR code for your authenticator app" src={stage.step.qr_svg} />
                <p className="secret">Can’t scan? Enter this key: {stage.step.secret}</p>
              </div>
            ) : null}
            {error ? <div className="alert alert-error" role="alert">{error}</div> : null}
            <div className="field">
              <label htmlFor="code">6-digit code</label>
              <input
                autoComplete="one-time-code"
                autoFocus
                className="code-input"
                id="code"
                inputMode="numeric"
                maxLength={7}
                onChange={(event) => setCode(event.target.value)}
                pattern="[0-9 ]{6,7}"
                required
                value={code}
              />
            </div>
            <button className="btn btn-primary btn-block" disabled={busy} type="submit">
              {busy ? 'Verifying…' : 'Sign in'}
            </button>
            <button className="link-btn" onClick={restart} type="button">
              Use a different account
            </button>
          </form>
        )}
      </section>
    </div>
  );
}
