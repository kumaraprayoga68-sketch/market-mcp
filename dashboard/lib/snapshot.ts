/**
 * Snapshot loading.
 *
 * The scheduled job commits `snapshots/latest.json` to the repo, so the
 * dashboard reads it straight from GitHub. That means new data appears without
 * a redeploy — the cron owns the data, Vercel only owns the rendering.
 */

export const REPO = "kumaraprayoga68-sketch/market-mcp";

const DEFAULT_URL = `https://raw.githubusercontent.com/${REPO}/main/snapshots/latest.json`;

/**
 * The snapshot fetch is deliberately uncached (`cache: "no-store"` below).
 *
 * A `revalidate` window means stale-while-revalidate: once it expires, the next
 * visitor is served the OLD page and their request is merely what triggers
 * regeneration. On a low-traffic personal dashboard you are almost always that
 * visitor, so you would routinely read the previous scan while believing it was
 * current — which defeats a page built around how fresh its data is.
 *
 * The cost is one ~80 KB fetch per view, which is nothing here.
 */

export type Quote = {
  name?: string;
  symbol: string;
  price: number | null;
  change_pct: number | null;
  currency?: string | null;
};

export type ScanRow = {
  symbol: string;
  price: number | null;
  rsi_14: number | null;
  rating: string;
  rating_score: number;
  trend_strength: string;
  volume_vs_20bar_avg: number | null;
  adx_14: number | null;
};

export type WatchRow = {
  symbol: string;
  market: string;
  price?: number | null;
  rating?: string;
  rating_score?: number;
  trend_strength?: string;
  rsi_14?: number | null;
  adx_14?: number | null;
  supertrend_direction?: string | null;
  ema_200?: number | null;
  atr_pct_of_price?: number | null;
  bullish_votes?: number;
  bearish_votes?: number;
  support_20bar?: number | null;
  resistance_20bar?: number | null;
  error?: string;
};

export type Outcome = {
  outcome: string;
  price: number | null;
  implied_probability_pct: number | null;
  token_id: string | null;
};

export type PredictionMarket = {
  id: string;
  slug: string | null;
  question: string;
  outcomes: Outcome[];
  volume_24h: number | null;
  liquidity: number | null;
  end_date: string | null;
  url: string | null;
};

export type ScanBlock = Record<
  string,
  { criterion: string; matched: number; results: ScanRow[] } | { requested: number; evaluated: number; skipped: number }
>;

export type Snapshot = {
  generated_at: string;
  duration_seconds: number;
  quick_mode: boolean;
  market: {
    groups?: Record<string, Quote[]>;
    biggest_movers?: Quote[];
  };
  crypto: {
    universe_size?: number;
    by_volume?: (Quote & { volume_quote_24h?: number | null; base?: string })[];
    gainers?: (Quote & { volume_quote_24h?: number | null })[];
    losers?: (Quote & { volume_quote_24h?: number | null })[];
  };
  idx: {
    scanned?: number;
    gainers?: Quote[];
    losers?: Quote[];
  };
  scans: Record<string, ScanBlock>;
  watchlist: WatchRow[];
  prediction: {
    top_by_volume?: PredictionMarket[];
    topics?: Record<string, PredictionMarket[] | { error: string }>;
  };
  backtests: {
    comparisons?: {
      symbol: string;
      market: string;
      bars: number;
      buy_and_hold_return_pct: number;
      ranking: {
        strategy: string;
        total_return_pct: number;
        sharpe: number | null;
        max_drawdown_pct: number;
        win_rate_pct: number | null;
        trades: number;
      }[];
    }[];
    walk_forward?: {
      symbol: string;
      strategy: string;
      verdict: { label: string; reason: string };
      summary: Record<string, unknown>;
    }[];
  };
  errors: { section: string; error: string }[];
};

export type LoadResult =
  | { ok: true; snapshot: Snapshot; source: string }
  | { ok: false; reason: string; source: string };

export async function loadSnapshot(): Promise<LoadResult> {
  const source = process.env.SNAPSHOT_URL || DEFAULT_URL;
  try {
    const res = await fetch(source, { cache: "no-store" });
    if (!res.ok) {
      return {
        ok: false,
        source,
        reason:
          res.status === 404
            ? "No snapshot has been published yet. Run the Scheduled scan workflow once."
            : `GitHub returned HTTP ${res.status}.`,
      };
    }
    return { ok: true, snapshot: (await res.json()) as Snapshot, source };
  } catch (e) {
    return { ok: false, source, reason: e instanceof Error ? e.message : "Fetch failed." };
  }
}

/** Scan sections carry a `_meta` key that is not a signal. */
export function signalEntries(block: ScanBlock | undefined) {
  if (!block) return [];
  return Object.entries(block).filter(
    ([key, value]) => key !== "_meta" && value && "results" in value,
  ) as [string, { criterion: string; matched: number; results: ScanRow[] }][];
}

export function scanMeta(block: ScanBlock | undefined) {
  const meta = block?._meta;
  if (meta && "evaluated" in meta) return meta;
  return null;
}
