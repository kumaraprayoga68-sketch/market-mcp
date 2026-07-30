import { compact, num, pct, price, titleCase, verdictTone } from "@/lib/format";
import type { PredictionMarket, ScanRow, Snapshot, WatchRow } from "@/lib/snapshot";
import { scanMeta, signalEntries } from "@/lib/snapshot";
import { Card, Change, Empty, Rating, Scroller, Section, Tile } from "./primitives";

const GROUP_TITLES: Record<string, string> = {
  us_equity: "US equity",
  volatility: "Volatility",
  asia: "Asia",
  crypto: "Crypto",
  fx_commodity: "FX & commodities",
};

export function MarketOverview({ snapshot }: { snapshot: Snapshot }) {
  const groups = snapshot.market?.groups ?? {};
  const entries = Object.entries(groups);
  if (!entries.length) return null;

  return (
    <Section title="Market overview" hint="change vs previous close">
      <div className="grid-compact">
        {entries.map(([group, quotes]) => (
          <Card key={group} title={GROUP_TITLES[group] ?? titleCase(group)}>
            <div className="tiles">
              {quotes.map((q) => (
                <Tile key={q.symbol} label={q.name ?? q.symbol} value={q.price} change={q.change_pct} />
              ))}
            </div>
          </Card>
        ))}
      </div>
    </Section>
  );
}

export function Watchlist({ rows }: { rows: WatchRow[] }) {
  if (!rows?.length) return null;
  const usable = rows.filter((r) => !r.error);

  return (
    <Section title="Watchlist" hint="composite rating from 7 indicator votes">
      <Card>
        <Scroller>
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Price</th>
                <th>Rating</th>
                <th>Votes</th>
                <th>RSI</th>
                <th>ADX</th>
                <th>Trend</th>
                <th>ATR%</th>
                <th>Support</th>
                <th>Resistance</th>
              </tr>
            </thead>
            <tbody>
              {usable.map((r) => (
                <tr key={`${r.market}-${r.symbol}`}>
                  <td>
                    <span className="sym">{r.symbol}</span>
                  </td>
                  <td>{price(r.price)}</td>
                  <td>
                    <Rating label={r.rating} />
                  </td>
                  <td>
                    <span className="up">{r.bullish_votes ?? 0}</span>
                    <span className="flat"> / </span>
                    <span className="down">{r.bearish_votes ?? 0}</span>
                  </td>
                  <td>{num(r.rsi_14, 0)}</td>
                  <td>{num(r.adx_14, 0)}</td>
                  <td className={r.supertrend_direction === "up" ? "up" : "down"}>
                    {r.supertrend_direction === "up" ? "▲" : r.supertrend_direction === "down" ? "▼" : "—"}
                    <span className="flat"> {r.trend_strength ?? ""}</span>
                  </td>
                  <td>{num(r.atr_pct_of_price, 1)}</td>
                  <td>{price(r.support_20bar)}</td>
                  <td>{price(r.resistance_20bar)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroller>
        {rows.length !== usable.length ? (
          <Empty>
            {rows.length - usable.length} symbol(s) could not be loaded this run:{" "}
            {rows.filter((r) => r.error).map((r) => r.symbol).join(", ")}
          </Empty>
        ) : null}
      </Card>
    </Section>
  );
}

const SCAN_ROWS_SHOWN = 8;

function ScanList({ rows, matched }: { rows: ScanRow[]; matched: number }) {
  if (!rows.length) return <Empty>No matches.</Empty>;

  const shown = rows.slice(0, SCAN_ROWS_SHOWN);
  // The card header states the full count, so say plainly when the list under
  // it is only part of that rather than letting the two numbers disagree.
  const hidden = matched - shown.length;

  return (
    <>
      <table>
        <tbody>
          {shown.map((r) => (
            <tr key={r.symbol}>
              <td>
                <span className="sym">{r.symbol}</span>
              </td>
              <td>{price(r.price)}</td>
              <td>{num(r.rsi_14, 0)}</td>
              <td>
                <Rating label={r.rating} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {hidden > 0 ? <Empty>+{hidden} more not shown</Empty> : null}
    </>
  );
}

export function Scans({ snapshot }: { snapshot: Snapshot }) {
  const markets = Object.entries(snapshot.scans ?? {});
  if (!markets.length) return null;

  return (
    <>
      {markets.map(([market, block]) => {
        const signals = signalEntries(block);
        const meta = scanMeta(block);
        if (!signals.length) return null;

        return (
          <Section
            key={market}
            title={`${market.toUpperCase()} scans`}
            hint={
              meta
                ? `${meta.evaluated} symbols evaluated${meta.skipped ? `, ${meta.skipped} skipped` : ""}`
                : undefined
            }
          >
            <div className="grid">
              {signals.map(([signal, payload]) => (
                <Card key={signal} title={`${titleCase(signal)} · ${payload.matched}`}>
                  <ScanList rows={payload.results} matched={payload.matched} />
                  <Empty>{payload.criterion}</Empty>
                </Card>
              ))}
            </div>
          </Section>
        );
      })}
    </>
  );
}

function MoverTable({
  rows,
  showVolume,
}: {
  rows: { symbol: string; name?: string; price: number | null; change_pct: number | null; volume_quote_24h?: number | null }[];
  showVolume?: boolean;
}) {
  if (!rows?.length) return <Empty>No data this run.</Empty>;
  return (
    <Scroller>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Price</th>
            <th>Change</th>
            {showVolume ? <th>24h vol</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td>
                <span className="sym">{r.symbol}</span>
                {r.name ? <div className="name">{r.name}</div> : null}
              </td>
              <td>{price(r.price)}</td>
              <td>
                <Change value={r.change_pct} />
              </td>
              {showVolume ? <td>{compact(r.volume_quote_24h)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </Scroller>
  );
}

export function Movers({ snapshot }: { snapshot: Snapshot }) {
  const { crypto, idx } = snapshot;
  return (
    <Section
      title="Movers"
      hint={crypto?.universe_size ? `${crypto.universe_size} liquid crypto pairs screened` : undefined}
    >
      <div className="grid-2">
        <Card title="Crypto gainers">
          <MoverTable rows={crypto?.gainers ?? []} showVolume />
        </Card>
        <Card title="Crypto losers">
          <MoverTable rows={crypto?.losers ?? []} showVolume />
        </Card>
        <Card title="IDX gainers">
          <MoverTable rows={idx?.gainers ?? []} />
        </Card>
        <Card title="IDX losers">
          <MoverTable rows={idx?.losers ?? []} />
        </Card>
      </div>
    </Section>
  );
}

const OUTCOME_COLOURS = ["var(--pos)", "var(--neg)", "var(--accent)", "var(--warn)"];

function MarketRow({ market }: { market: PredictionMarket }) {
  const outcomes = (market.outcomes ?? []).filter((o) => o.implied_probability_pct != null);
  return (
    <div className="market-row">
      {market.url ? (
        <a className="market-q" href={market.url} target="_blank" rel="noopener noreferrer">
          {market.question}
        </a>
      ) : (
        <span className="market-q">{market.question}</span>
      )}
      <div className="bar">
        {outcomes.map((o, i) => (
          <span
            key={o.outcome}
            style={{
              width: `${o.implied_probability_pct}%`,
              background: OUTCOME_COLOURS[i % OUTCOME_COLOURS.length],
            }}
          />
        ))}
      </div>
      <div className="market-legend">
        {outcomes.map((o) => (
          <span key={o.outcome}>
            {o.outcome} {o.implied_probability_pct?.toFixed(1)}%
          </span>
        ))}
        {market.volume_24h ? <span>vol {compact(market.volume_24h)}</span> : null}
      </div>
    </div>
  );
}

export function Prediction({ snapshot }: { snapshot: Snapshot }) {
  const top = snapshot.prediction?.top_by_volume ?? [];
  const topics = snapshot.prediction?.topics ?? {};
  if (!top.length && !Object.keys(topics).length) return null;

  return (
    <Section title="Prediction markets" hint="prices are probabilities — 0.63 means the market says 63%">
      <div className="grid-2">
        {top.length ? (
          <Card title="Most active">
            {top.slice(0, 8).map((m) => (
              <MarketRow key={m.id} market={m} />
            ))}
          </Card>
        ) : null}
        {Object.entries(topics).map(([topic, markets]) => {
          if (!Array.isArray(markets)) {
            return (
              <Card key={topic} title={titleCase(topic)}>
                <Empty>Search failed this run: {markets.error}</Empty>
              </Card>
            );
          }
          if (!markets.length) {
            return (
              <Card key={topic} title={titleCase(topic)}>
                <Empty>No active markets matched.</Empty>
              </Card>
            );
          }
          return (
            <Card key={topic} title={titleCase(topic)}>
              {markets.slice(0, 5).map((m) => (
                <MarketRow key={m.id} market={m} />
              ))}
            </Card>
          );
        })}
      </div>
    </Section>
  );
}

export function Backtests({ snapshot }: { snapshot: Snapshot }) {
  const comparisons = snapshot.backtests?.comparisons ?? [];
  const walkForward = snapshot.backtests?.walk_forward ?? [];
  if (!comparisons.length && !walkForward.length) return null;

  return (
    <Section title="Backtests" hint="historical simulation with costs — not a prediction">
      <div className="grid-2">
        {comparisons.map((c) => (
          <Card key={c.symbol} title={`${c.symbol} · ${c.bars} daily bars`}>
            <Scroller>
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Return</th>
                    <th>Sharpe</th>
                    <th>Max DD</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {c.ranking.slice(0, 6).map((r) => (
                    <tr key={r.strategy}>
                      <td>
                        <span className="sym">{r.strategy}</span>
                      </td>
                      <td>
                        <Change value={r.total_return_pct} digits={1} />
                      </td>
                      <td>{num(r.sharpe, 2)}</td>
                      <td className="down">{pct(-Math.abs(r.max_drawdown_pct), 1)}</td>
                      <td>{r.trades}</td>
                    </tr>
                  ))}
                  <tr>
                    <td>
                      <span className="sym flat">buy &amp; hold</span>
                    </td>
                    <td>
                      <Change value={c.buy_and_hold_return_pct} digits={1} />
                    </td>
                    <td colSpan={3} className="flat">
                      benchmark
                    </td>
                  </tr>
                </tbody>
              </table>
            </Scroller>
          </Card>
        ))}

        {walkForward.length ? (
          <Card title="Walk-forward validation">
            {walkForward.map((w) => (
              <div key={`${w.symbol}-${w.strategy}`} className="market-row">
                <span className="market-q">
                  <span className="sym">{w.symbol}</span> · {w.strategy}{" "}
                  <span className={`badge ${verdictTone(w.verdict?.label)}`}>
                    {titleCase(w.verdict?.label ?? "unknown")}
                  </span>
                </span>
                <div className="market-legend">{w.verdict?.reason}</div>
              </div>
            ))}
            <Empty>
              Parameters are re-optimised on data up to each fold and scored on the fold after,
              so these numbers come from bars the optimiser never saw.
            </Empty>
          </Card>
        ) : null}
      </div>
    </Section>
  );
}
