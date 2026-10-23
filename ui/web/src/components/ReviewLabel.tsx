/** The label of a "reviewed" toggle. Every state sits in the same grid cell, so the button keeps the width of the
 *  longest one and nothing around it moves when it flips; while saving, a spinner shows instead. */
export function ReviewLabel({ reviewed, saving = false }: { reviewed: boolean; saving?: boolean }) {
  const shown = saving ? "saving" : reviewed ? "on" : "off";
  return (
    <span className="review-label">
      <span className={shown === "off" ? "" : "is-hidden"}>Mark reviewed</span>
      <span className={shown === "on" ? "" : "is-hidden"}>✓ Reviewed</span>
      <span className={shown === "saving" ? "" : "is-hidden"} aria-label="Saving">
        <span className="spinner" />
      </span>
    </span>
  );
}
