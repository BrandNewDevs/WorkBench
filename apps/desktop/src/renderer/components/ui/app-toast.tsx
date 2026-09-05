import { AnimatePresence, domAnimation, LazyMotion, m, useReducedMotion } from "motion/react";
import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

export type ToastAction = {
  label: string;
  onClick: () => void;
};

export type AppToastProps = {
  action?: ToastAction;
  description: string;
  onDismiss: () => void;
  title: string;
};

const errorToastDurationMs = 8_000;

export function AppToast({ action, description, onDismiss, title }: AppToastProps) {
  const reduceMotion = useReducedMotion();
  const [visible, setVisible] = useState(true);
  const remainingMsRef = useRef(errorToastDurationMs);
  const startedAtRef = useRef<number | undefined>(undefined);
  const timerRef = useRef<number | undefined>(undefined);
  const hostRef = useRef<HTMLDivElement>(null);
  const pointerInsideRef = useRef(false);
  const focusWithinRef = useRef(false);

  const dismiss = useCallback(() => setVisible(false), []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  const startTimer = useCallback(() => {
    if (timerRef.current !== undefined || !visible || remainingMsRef.current <= 0 || document.visibilityState === "hidden") return;

    startedAtRef.current = Date.now();
    timerRef.current = window.setTimeout(dismiss, remainingMsRef.current);
  }, [dismiss, visible]);

  const pauseTimer = useCallback(() => {
    clearTimer();
    if (startedAtRef.current !== undefined) {
      remainingMsRef.current = Math.max(0, remainingMsRef.current - (Date.now() - startedAtRef.current));
      startedAtRef.current = undefined;
    }
  }, [clearTimer]);

  const resumeTimer = useCallback(() => {
    if (!pointerInsideRef.current && !focusWithinRef.current) startTimer();
  }, [startTimer]);

  useEffect(() => {
    startTimer();
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") pauseTimer();
      else resumeTimer();
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      clearTimer();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [clearTimer, pauseTimer, resumeTimer, startTimer]);

  useEffect(() => {
    const handleFocusIn = (event: FocusEvent) => {
      const toastElement = hostRef.current?.parentElement;
      if (toastElement?.contains(event.target instanceof Node ? event.target : null)) {
        focusWithinRef.current = true;
        pauseTimer();
      }
    };
    const handleFocusOut = (event: FocusEvent) => {
      const toastElement = hostRef.current?.parentElement;
      if (toastElement?.contains(event.target instanceof Node ? event.target : null)) {
        if (toastElement.contains(event.relatedTarget instanceof Node ? event.relatedTarget : null)) return;
        focusWithinRef.current = false;
        resumeTimer();
      }
    };

    document.addEventListener("focusin", handleFocusIn);
    document.addEventListener("focusout", handleFocusOut);
    return () => {
      document.removeEventListener("focusin", handleFocusIn);
      document.removeEventListener("focusout", handleFocusOut);
    };
  }, [pauseTimer, resumeTimer]);

  return (
    <div className="workbench-toast-motion-host" ref={hostRef}>
      <LazyMotion features={domAnimation}>
        <AnimatePresence onExitComplete={onDismiss}>
          {visible ? (
            <m.div
              animate={{ opacity: 1 }}
              className="workbench-toast-content"
              exit={reduceMotion ? { opacity: 0 } : { filter: "blur(2px)", opacity: 0 }}
              initial={{ opacity: 0 }}
              onPointerEnter={() => {
                pointerInsideRef.current = true;
                pauseTimer();
              }}
              onPointerLeave={() => {
                pointerInsideRef.current = false;
                resumeTimer();
              }}
              role="alert"
              transition={{ duration: reduceMotion ? 0.12 : 0.16, ease: [0.23, 1, 0.32, 1] }}
            >
              <div className="min-w-0 pr-7">
                <p className="text-sm font-medium text-foreground">{title}</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
              </div>
              <button
                aria-label="Dismiss notification"
                className="workbench-toast-dismiss"
                onClick={dismiss}
                type="button"
              >
                <X aria-hidden="true" className="size-4" strokeWidth={1.75} />
              </button>
              {action && (
                <button
                  className="workbench-toast-action"
                  onClick={() => {
                    action.onClick();
                    dismiss();
                  }}
                  type="button"
                >
                  {action.label}
                </button>
              )}
            </m.div>
          ) : null}
        </AnimatePresence>
      </LazyMotion>
    </div>
  );
}
