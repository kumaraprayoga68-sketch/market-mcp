import { direction, pct, price, ratingTone, titleCase } from "@/lib/format";

export function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {hint ? <span className="hint">{hint}</span> : null}
      </div>
      {children}
    </section>
  );
}

export function Card({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <div className="card">
      {title ? <h3>{title}</h3> : null}
      {children}
    </div>
  );
}

/** A signed percentage, coloured by direction. */
export function Change({ value, digits = 2 }: { value: number | null | undefined; digits?: number }) {
  return <span className={direction(value)}>{pct(value, digits)}</span>;
}

export function Rating({ label }: { label: string | undefined }) {
  if (!label) return <span className="flat">—</span>;
  return <span className={`badge ${ratingTone(label)}`}>{titleCase(label)}</span>;
}

export function Tile({
  label,
  value,
  change,
}: {
  label: string;
  value: number | null | undefined;
  change: number | null | undefined;
}) {
  return (
    <div className="tile">
      <div className="label" title={label}>
        {label}
      </div>
      <div className="value">{price(value)}</div>
      <div className={`delta ${direction(change)}`}>{pct(change)}</div>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** Wraps a wide table so it scrolls inside its card instead of the page. */
export function Scroller({ children }: { children: React.ReactNode }) {
  return <div className="scroller">{children}</div>;
}
