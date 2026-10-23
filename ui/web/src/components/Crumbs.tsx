import { useEffect, useRef, type ReactNode } from "react";

export interface Crumb {
  label: string;
  onClick?: () => void;
  /** Rendered instead of the label (e.g. a title edited in place) */
  node?: ReactNode;
}

/** The bar pinned under the top: where you are (All days › the day › the account › the topic) on the left, and
 *  the day's state and next step on the right. Its height is kept in --crumbs-height for the sticky bits below. */
export function Crumbs({ items, children }: { items: Crumb[]; children?: ReactNode }) {
  const bar = useRef<HTMLElement>(null);
  useEffect(() => {
    const element = bar.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      document.documentElement.style.setProperty("--crumbs-height", `${element.offsetHeight}px`),
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty("--crumbs-height");
    };
  }, []);

  return (
    <nav className="crumbs" ref={bar} aria-label="Where you are">
      <ol className="crumbs__trail">
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={`${index}-${item.label}`} className={`crumbs__item${last ? " is-current" : ""}`}>
              {item.node ? (
                item.node
              ) : item.onClick && !last ? (
                <button className="crumbs__link" onClick={item.onClick} title={item.label}>
                  {item.label}
                </button>
              ) : (
                <span title={item.label} aria-current={last ? "location" : undefined}>
                  {item.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      {children && <div className="crumbs__side">{children}</div>}
    </nav>
  );
}

export function StatePill({ label, tone }: { label: string; tone: string }) {
  return <span className={`state state--${tone}`}>{label}</span>;
}

export function Progress({ done, total, label }: { done: number; total: number; label?: string }) {
  const share = total ? Math.round((done / total) * 100) : 0;
  return (
    <span className="progress" title={`${done} of ${total} reviewed`}>
      <span className="progress__track">
        <span className={`progress__fill${done === total && total > 0 ? " is-done" : ""}`} style={{ width: `${share}%` }} />
      </span>
      <span className="progress__text">
        {done}/{total}
        {label ? ` ${label}` : ""}
      </span>
    </span>
  );
}
