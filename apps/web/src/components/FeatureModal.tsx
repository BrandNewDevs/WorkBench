export default function FeatureModal({ feature, onClose }: { feature: { title: string; desc: string } | null; onClose: () => void }) {
  if (!feature) return null;

  return (
    <div className="feature-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="feature-modal" onClick={(e) => e.stopPropagation()}>
        <button className="feature-modal-close" onClick={onClose} aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M1 1L13 13M1 13L13 1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        <span className="feature-modal-tag">{feature.title === "Local AI" ? "Local AI" : "Other Features"}</span>
        <h2 className="feature-modal-title">{feature.title}</h2>
        <p className="feature-modal-desc">{feature.desc}</p>
      </div>
    </div>
  );
}