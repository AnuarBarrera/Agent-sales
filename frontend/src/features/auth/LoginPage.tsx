import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "./useAuth";
import { ApiError } from "../../lib/api";

type PublicConfig = { enabled: boolean; otp_length: 6; expires_in: 600; resend_after: 60 };
type Challenge = { challenge_id: string; expires_in: number; resend_after: number };
type StoredOtp = { email: string; challenge_id: string; expires_at: number; resend_at: number };
const OTP_STORAGE_KEY = "tuwebmx.demo-otp";

function clearStoredOtp() {
  try { sessionStorage.removeItem(OTP_STORAGE_KEY); } catch { /* session storage can be unavailable */ }
}

function restoreOtp(): { email: string; challenge: Challenge | null; remaining: number; expiresRemaining: number } {
  try {
    const stored = JSON.parse(sessionStorage.getItem(OTP_STORAGE_KEY) ?? "null") as StoredOtp | null;
    if (!stored || typeof stored.email !== "string" || typeof stored.challenge_id !== "string"
      || !Number.isFinite(stored.expires_at) || !Number.isFinite(stored.resend_at)) {
      if (stored !== null) clearStoredOtp();
      return { email: "", challenge: null, remaining: 0, expiresRemaining: 0 };
    }
    const now = Date.now();
    const expiresRemaining = Math.max(0, Math.ceil((stored.expires_at - now) / 1000));
    if (expiresRemaining <= 0) {
      clearStoredOtp();
      return { email: "", challenge: null, remaining: 0, expiresRemaining: 0 };
    }
    return {
      email: stored.email,
      challenge: { challenge_id: stored.challenge_id, expires_in: expiresRemaining, resend_after: Math.max(0, Math.ceil((stored.resend_at - now) / 1000)) },
      remaining: Math.max(0, Math.ceil((stored.resend_at - now) / 1000)),
      expiresRemaining,
    };
  } catch {
    return { email: "", challenge: null, remaining: 0, expiresRemaining: 0 };
  }
}

export function LoginPage() {
  const { login, logout, verifyDemoOtp, demo, isLoading } = useAuth();
  const navigate = useNavigate();
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [restoredOtp] = useState(restoreOtp);
  const [email, setEmail] = useState(restoredOtp.email);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState<Challenge | null>(restoredOtp.challenge);
  const [remaining, setRemaining] = useState(restoredOtp.remaining);
  const [expiresRemaining, setExpiresRemaining] = useState(restoredOtp.expiresRemaining);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previousSessionRequired, setPreviousSessionRequired] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const verifyFlight = useRef(false);

  useEffect(() => {
    let live = true;
    fetch("/api/v1/auth/demo/config", { credentials: "include", headers: { Accept: "application/json" } })
      .then(async (response) => { if (!response.ok) throw new Error("No se pudo consultar el acceso demo."); return response.json() as Promise<PublicConfig>; })
      .then((data) => {
        if (typeof data.enabled !== "boolean") throw new Error("El servidor devolvió una configuración de acceso inválida.");
        if (live) {
          if (!data.enabled) { clearStoredOtp(); setEmail(""); setChallenge(null); setCode(""); setRemaining(0); setExpiresRemaining(0); }
          setConfig(data);
        }
      })
      .catch((reason: unknown) => { if (live) setConfigError(reason instanceof Error ? reason.message : "Acceso no disponible."); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (remaining <= 0 && expiresRemaining <= 0) return;
    const timer = window.setTimeout(() => {
      setRemaining((value) => Math.max(0, value - 1));
      setExpiresRemaining((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [remaining, expiresRemaining]);
  useEffect(() => { if (demo?.enabled) navigate("/demo", { replace: true }); }, [demo, navigate]);

  function showAuthError(reason: unknown) {
    const isPreviousSession = reason instanceof ApiError
      && (reason.code === "active_session_present" || reason.code === "csrf_token_required");
    setPreviousSessionRequired(isPreviousSession);
    setError(reason instanceof Error ? reason.message : "No se pudo completar el acceso.");
  }

  async function closePreviousSession() {
    setLogoutBusy(true); setError("");
    try {
      await logout();
      setPreviousSessionRequired(false);
    } catch (reason) {
      setPreviousSessionRequired(true);
      setError(reason instanceof Error ? reason.message : "No se pudo cerrar la sesión anterior.");
    } finally { setLogoutBusy(false); }
  }

  async function requestCode(event?: React.FormEvent) {
    event?.preventDefault();
    setError(""); setBusy(true);
    try {
      const response = await fetch("/api/v1/auth/demo/otp/request", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ email: email.trim() }) });
      if (!response.ok && response.status === 429) setRemaining(Math.max(1, Number(response.headers.get("Retry-After")) || 60));
      const payload = await response.json();
      if (!response.ok) {
        const detail = payload.detail;
        const code = typeof detail?.code === "string" ? detail.code
          : response.status === 403 && detail === "CSRF token required" ? "csrf_token_required" : undefined;
        throw new ApiError(response.status, detail?.message ?? detail ?? "No se pudo enviar el código.", code);
      }
      const nextChallenge = payload as Challenge;
      const nextEmail = email.trim();
      const expiresIn = nextChallenge.expires_in ?? 600;
      const resendAfter = nextChallenge.resend_after ?? 60;
      setChallenge(nextChallenge); setRemaining(resendAfter); setExpiresRemaining(expiresIn); setCode("");
      try { sessionStorage.setItem(OTP_STORAGE_KEY, JSON.stringify({ email: nextEmail, challenge_id: nextChallenge.challenge_id, expires_at: Date.now() + expiresIn * 1000, resend_at: Date.now() + resendAfter * 1000 } satisfies StoredOtp)); } catch { /* login continues without session storage */ }
    } catch (reason) { showAuthError(reason); }
    finally { setBusy(false); }
  }

  async function submitCode(event: React.FormEvent) {
    event.preventDefault();
    if (!challenge || expiresRemaining <= 0 || verifyFlight.current) return;
    verifyFlight.current = true; setBusy(true); setError("");
    try { await verifyDemoOtp(challenge.challenge_id, code); clearStoredOtp(); navigate("/demo", { replace: true }); }
    catch (reason) { showAuthError(reason); }
    finally { verifyFlight.current = false; setBusy(false); }
  }

  async function submitPassword(event: React.FormEvent) {
    event.preventDefault(); setError(""); setBusy(true);
    try { await login(email, password); navigate("/prospection/new", { replace: true }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "No se pudo iniciar sesión."); }
    finally { setBusy(false); }
  }

  return <main className="auth-page"><Link to="/" className="demo-brand"><span className="demo-mark">T</span><span>Tu Web MX</span></Link><section className="auth-panel">
    <p className="demo-kicker">{config?.enabled ? "Acceso a la demo" : "Acceso al espacio de trabajo"}</p>
    <h1>{config?.enabled ? "Entra con tu correo" : "Iniciar sesión"}</h1>
    {configError ? <div role="alert" className="auth-error">{configError}</div> : config === null ? <p role="status">Consultando disponibilidad…</p> : config.enabled ? <>
      <p className="auth-description">Te enviaremos un código de seis dígitos. No necesitas crear una contraseña.</p>
      {error && <p role="alert" className="auth-error">{error}</p>}
      {!challenge ? <form onSubmit={(event) => void requestCode(event)} className="auth-form"><label htmlFor="demo-email">Correo electrónico</label><input id="demo-email" type="email" autoComplete="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} className="input" disabled={busy || logoutBusy} placeholder="tu@correo.com" /><button className="btn-primary" disabled={busy || logoutBusy || previousSessionRequired}>{busy ? "Enviando código…" : "Enviar código"}</button></form> : <form onSubmit={(event) => void submitCode(event)} className="auth-form"><p className="auth-description">Código enviado a <strong>{email}</strong>. {expiresRemaining > 0 ? `Caduca en ${Math.ceil(expiresRemaining / 60)} min.` : "El código caducó. Solicita uno nuevo."}</p><label htmlFor="demo-code">Código de verificación</label><input id="demo-code" aria-label="Código de verificación" type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} className="input otp-input" disabled={busy || logoutBusy || expiresRemaining <= 0} /><button className="btn-primary" disabled={busy || logoutBusy || previousSessionRequired || code.length !== 6 || expiresRemaining <= 0}>{busy ? "Verificando…" : "Verificar y entrar"}</button><button type="button" className="auth-text-button" disabled={busy || logoutBusy || remaining > 0} onClick={() => void requestCode()}>{remaining > 0 ? `Reenviar en ${remaining}s` : "Enviar un código nuevo"}</button><button type="button" className="auth-text-button" disabled={busy || logoutBusy} onClick={() => { clearStoredOtp(); setChallenge(null); setCode(""); setError(""); setPreviousSessionRequired(false); }}>Usar otro correo</button></form>}
      {previousSessionRequired && <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 p-4"><p className="text-sm text-amber-950">Cierra la sesión anterior antes de verificar este código. El código actual se conservará.</p><button type="button" className="btn-secondary mt-3" disabled={logoutBusy || busy} onClick={() => void closePreviousSession()}>{logoutBusy ? "Cerrando sesión…" : "Cerrar sesión anterior"}</button></div>}
    </> : <><p className="auth-description">Usa las credenciales de tu espacio de trabajo.</p>{error && <p role="alert" className="auth-error">{error}</p>}<form onSubmit={(event) => void submitPassword(event)} className="auth-form"><label htmlFor="internal-email">Correo electrónico</label><input id="internal-email" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} className="input" disabled={busy || isLoading} /><label htmlFor="internal-password">Contraseña</label><input id="internal-password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} className="input" disabled={busy || isLoading} /><button className="btn-primary" disabled={busy || isLoading}>{busy ? "Entrando…" : "Iniciar sesión"}</button></form></>}
  </section></main>;
}
