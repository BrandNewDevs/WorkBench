import { useEffect, useRef } from "react";

export interface Feature {
  title: string;
  desc: string;
}

export default function FeatureModal({ feature, onClose }: { feature: Feature | null; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  if (feature && restoreFocusRef.current === null && document.activeElement instanceof HTMLElement) {
    restoreFocusRef.current = document.activeElement;
  }

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    const previousHtmlOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    dialog.showModal();
    closeButtonRef.current?.focus();

    return () => {
      if (dialog.open) dialog.close();
      document.documentElement.style.overflow = previousHtmlOverflow;
      document.body.style.overflow = previousBodyOverflow;
      if (restoreFocusRef.current?.isConnected) restoreFocusRef.current.focus();
    };
  }, []);

  if (!feature) return null;

  return (
    <dialog
      ref={dialogRef}
      className="feature-modal"
      aria-modal="true"
      aria-labelledby="feature-modal-title"
      aria-describedby="feature-modal-description"
      onCancel={(event) => {
        event.preventDefault();
        onCloseRef.current();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCloseRef.current();
      }}
    >
      <div className="feature-modal-content">
        <button ref={closeButtonRef} className="feature-modal-close" type="button" onClick={onCloseRef.current} aria-label="Close feature details">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M1 1L13 13M1 13L13 1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        <span className="feature-modal-tag">{feature.title}</span>
        <h2 id="feature-modal-title" className="feature-modal-title">{feature.title}</h2>
        <p id="feature-modal-description" className="feature-modal-desc">{feature.desc}</p>
      </div>
    </dialog>
  );
}
