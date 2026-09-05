export default function FeatureCard({ id, title, onClick }: { id: string; title: string; onClick: (id: string) => void }) {
  return (
    <article className="feature-card" onClick={() => onClick(id)} role="button" tabIndex={0} aria-label={`View ${title} details`} onKeyDown={(e) => { if (e.key === "Enter") onClick(id); }}>
      <span>{title === "Local AI" ? "Local AI" : "Other Features"}</span>
      <h2>{title}</h2>
    </article>
  );
}