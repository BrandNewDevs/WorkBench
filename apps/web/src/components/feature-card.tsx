export default function FeatureCard({ id, title, category, desc, icon, onClick }: {
  id: string;
  title: string;
  category: string;
  desc: string;
  icon: JSX.Element;
  onClick: (id: string) => void;
}) {
  return (
    <button
      type="button"
      className="feature-card"
      onClick={() => onClick(id)}
      aria-label={`View ${title} details`}
    >
      <div className="feature-card-icon">{icon}</div>
      <span className="feature-card-category">{category}</span>
      <span className="feature-card-title">{title}</span>
      <p className="feature-card-desc">{desc}</p>
    </button>
  );
}
