import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  getAuthConfig,
  getMe,
  loginWithGoogle,
  UNAUTHORIZED_EVENT,
  type AuthConfig,
  type AuthUser,
} from "../../api/client";
import { useAuthStore } from "../../stores/authStore";

declare global {
  interface Window {
    google?: any;
  }
}

type GateState = "loading" | "login" | "ready";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>("loading");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const setUser = useAuthStore((s) => s.setUser);

  const bootstrap = useCallback(async () => {
    try {
      const cfg = await getAuthConfig();
      setConfig(cfg);
      if (!cfg.auth_required) {
        setState("ready");
        return;
      }
      try {
        const user = await getMe();
        setUser(user);
        setState("ready");
      } catch {
        setState("login");
      }
    } catch {
      // Backend unreachable — let the app render its own connection errors.
      setState("ready");
    }
  }, [setUser]);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    const onUnauthorized = () => {
      setUser(null);
      setState((prev) => (prev === "ready" ? "login" : prev));
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [setUser]);

  const handleCredential = useCallback(
    async (credential: string) => {
      setError(null);
      try {
        const user = await loginWithGoogle(credential);
        setUser(user);
        setState("ready");
      } catch (e: any) {
        setError(e.message || "Sign-in failed");
      }
    },
    [setUser]
  );

  if (state === "loading") {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50">
        <Loader2 className="w-8 h-8 text-sunshine-400 animate-spin" />
      </div>
    );
  }

  if (state === "login" && config) {
    return <LoginScreen config={config} error={error} onCredential={handleCredential} />;
  }

  return <>{children}</>;
}

function LoginScreen({
  config,
  error,
  onCredential,
}: {
  config: AuthConfig;
  error: string | null;
  onCredential: (credential: string) => void;
}) {
  const buttonRef = useRef<HTMLDivElement>(null);
  const [gsiError, setGsiError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const renderButton = () => {
      if (cancelled || !window.google || !buttonRef.current) return;
      window.google.accounts.id.initialize({
        client_id: config.client_id,
        callback: (response: { credential: string }) => onCredential(response.credential),
      });
      window.google.accounts.id.renderButton(buttonRef.current, {
        theme: "outline",
        size: "large",
        width: 280,
      });
    };

    if (window.google?.accounts?.id) {
      renderButton();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = renderButton;
    script.onerror = () => setGsiError("Could not load Google sign-in. Check your connection.");
    document.head.appendChild(script);
    return () => {
      cancelled = true;
    };
  }, [config.client_id, onCredential]);

  return (
    <div className="h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-10 w-[380px] text-center">
        <div className="text-4xl mb-2">☀️</div>
        <h1 className="text-2xl font-bold text-sunshine-600 mb-1">Sunshine</h1>
        <p className="text-sm text-gray-500 mb-8">
          Sign in with your {config.allowed_domain} Google account
        </p>
        <div ref={buttonRef} className="flex justify-center min-h-[44px]" />
        {(error || gsiError) && (
          <p className="mt-4 text-sm text-red-600">{error || gsiError}</p>
        )}
      </div>
    </div>
  );
}
