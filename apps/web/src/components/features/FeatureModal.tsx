import { useEffect, useRef } from "react";
import { X } from "lucide-react";

export interface Feature {
  title: string;
  desc: string;
}

export default function FeatureModal({ feature, onClose }: { feature: Feature | null; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
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
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
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
        <button ref={closeButtonRef} className="feature-modal-close" type="button" onClick={() => onCloseRef.current()} aria-label="Close feature details">
          <X size={14} aria-hidden="true" />
        </button>
        <span className="feature-modal-tag">{feature.title}</span>
        <h2 id="feature-modal-title" className="feature-modal-title">{feature.title}</h2>
        <p id="feature-modal-description" className="feature-modal-desc">{feature.desc}</p>
      </div>
    </dialog>
  );
}
