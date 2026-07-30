/** Presentation helpers shared across the dashboard. */

/** Prices span BTC at ~65,000 and altcoins at 0.000012, so precision adapts. */
export function price(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1000) return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (Math.abs(value) >= 1) return value.toFixed(2);
  return value.toPrecision(3);
}

export function pct(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

export function compact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

/** "up" | "down" | "flat" — drives the colour of a change figure. */
export function direction(value: number | null | undefined): "up" | "down" | "flat" {
  if (value == null || !Number.isFinite(value) || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

/** Maps a composite rating label onto a colour band. */
export function ratingTone(label: string | undefined): string {
  switch (label) {
    case "strong_buy":
    case "buy":
      return "pos";
    case "weak_buy":
      return "pos-weak";
    case "strong_sell":
    case "sell":
      return "neg";
    case "weak_sell":
      return "neg-weak";
    default:
      return "flat";
  }
}

export function verdictTone(label: string | undefined): string {
  switch (label) {
    case "robust":
      return "pos";
    case "acceptable":
      return "pos-weak";
    case "fragile":
      return "neg-weak";
    case "overfitted":
    case "likely_overfitted":
      return "neg";
    default:
      return "flat";
  }
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** "3h ago" — the dashboard's most important number is how stale it is. */
export function relativeAge(isoUtc: string): { text: string; hours: number } {
  // The scan writes naive UTC timestamps; mark them as UTC before parsing.
  const stamp = isoUtc.endsWith("Z") ? isoUtc : `${isoUtc}Z`;
  const ms = Date.now() - new Date(stamp).getTime();
  const hours = ms / 3_600_000;

  if (!Number.isFinite(hours)) return { text: "unknown", hours: Infinity };
  const minutes = Math.round(ms / 60_000);
  if (minutes < 1) return { text: "just now", hours };
  if (minutes < 60) return { text: `${minutes}m ago`, hours };
  if (hours < 48) return { text: `${Math.round(hours)}h ago`, hours };
  return { text: `${Math.round(hours / 24)}d ago`, hours };
}
