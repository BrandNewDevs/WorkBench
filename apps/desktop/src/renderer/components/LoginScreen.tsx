import { useRef, useState, type FormEvent } from "react";
import type { EmployeeLoginRequest, EmployeeSession } from "../../shared/contracts";
import { LocalApiError, localApi } from "../api/localApi";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

type LoginScreenProps = {
  apiBaseUrl: string;
  initialMessage?: string;
  onAuthenticated: (session: EmployeeSession) => void;
};

type LoginField = "username" | "password";
type FieldErrors = Partial<Record<LoginField, string>>;

export function LoginScreen({ apiBaseUrl, initialMessage, onAuthenticated }: LoginScreenProps) {
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [loginError, setLoginError] = useState<string | undefined>();
  const [isSubmitting, setIsSubmitting] = useState(false);

  function clearFieldError(field: LoginField): void {
    setFieldErrors((current) => {
      if (!current[field]) {
        return current;
      }
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  async function submitLogin(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const request: EmployeeLoginRequest = { username: username.trim(), password };
    const nextFieldErrors: FieldErrors = {};

    if (!request.username) {
      nextFieldErrors.username = "Enter your employee ID.";
    }
    if (!request.password) {
      nextFieldErrors.password = "Enter your password.";
    }

    if (Object.keys(nextFieldErrors).length > 0) {
      setFieldErrors(nextFieldErrors);
      setLoginError(undefined);
      if (nextFieldErrors.username) {
        usernameRef.current?.focus();
      } else {
        passwordRef.current?.focus();
      }
      return;
    }

    setFieldErrors({});
    setLoginError(undefined);
    setIsSubmitting(true);
    try {
      const session = await localApi.login(request, apiBaseUrl);
      onAuthenticated(session);
    } catch (error) {
      setLoginError(error instanceof LocalApiError ? error.message : "Local sign-in failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex h-full w-full items-center justify-center bg-background px-6 py-12 text-foreground">
      <section aria-labelledby="login-heading" className="w-full max-w-md">
        <header className="space-y-2">
          <h1 id="login-heading" className="text-[1.75rem] font-semibold tracking-tight text-foreground">
            Sign in to WorkBench
          </h1>
          <p className="text-sm leading-5 text-muted-foreground">Use your employee account to continue.</p>
        </header>

        {initialMessage && (
          <p className="mt-5 text-sm leading-5 text-muted-foreground" aria-live="polite" role="status">
            {initialMessage}
          </p>
        )}

        <form className="mt-8 space-y-5" noValidate onSubmit={(event) => void submitLogin(event)}>
          <div className="space-y-2">
            <Label htmlFor="employee-username">Employee ID</Label>
            <Input
              ref={usernameRef}
              aria-describedby={fieldErrors.username ? "employee-username-error" : undefined}
              aria-invalid={Boolean(fieldErrors.username)}
              autoComplete="username"
              autoFocus
              disabled={isSubmitting}
              id="employee-username"
              name="username"
              onChange={(event) => {
                setUsername(event.target.value);
                clearFieldError("username");
              }}
              required
              spellCheck={false}
              value={username}
            />
            {fieldErrors.username && (
              <p className="text-sm leading-5 text-muted-foreground" id="employee-username-error" role="alert">
                {fieldErrors.username}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="employee-password">Password</Label>
            <Input
              ref={passwordRef}
              aria-describedby={fieldErrors.password ? "employee-password-error" : undefined}
              aria-invalid={Boolean(fieldErrors.password)}
              autoComplete="current-password"
              disabled={isSubmitting}
              id="employee-password"
              name="password"
              onChange={(event) => {
                setPassword(event.target.value);
                clearFieldError("password");
              }}
              required
              type="password"
              value={password}
            />
            {fieldErrors.password && (
              <p className="text-sm leading-5 text-muted-foreground" id="employee-password-error" role="alert">
                {fieldErrors.password}
              </p>
            )}
          </div>

          {loginError && (
            <p aria-live="polite" className="text-sm leading-5 text-muted-foreground" id="login-error" role="alert">
              {loginError}
            </p>
          )}

          <Button className="mt-1 h-10 w-full" disabled={isSubmitting} type="submit">
            {isSubmitting ? "Signing in..." : "Sign in"}
          </Button>
        </form>
      </section>
    </main>
  );
}
