/** The design system's section badge: a tiny coral pill and a mono uppercase label. */
export function Badge({ label }: { label: string }) {
  return (
    <span className="cw-badge">
      <span className="cw-badge__pill" />
      <span className="cw-badge__label">{label}</span>
    </span>
  );
}
