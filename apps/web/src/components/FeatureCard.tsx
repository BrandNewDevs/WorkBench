export default function FeatureCard({ id, title, onClick }: { id: string; title: string; onClick: (id: string) => void }) {
  return (
    <button
      type="button"
      className="feature-card"
      onClick={() => onClick(id)}
      aria-label={`View ${title} details`}
    >
      <span>{title === "Local AI" ? "Local AI" : "Other features"}</span>
      <span className="feature-card-title">{title}</span>
    </button>
  );
}
