import { relativeAge } from "@/lib/format";
import { loadSnapshot, REPO } from "@/lib/snapshot";
import { Backtests, MarketOverview, Movers, Prediction, Scans, Watchlist } from "./components/sections";

// Rendered per request. `cache: "no-store"` on the snapshot fetch already forces
// this, but saying it here keeps the intent obvious: a page whose headline fact
// is how old its data is must never be served from a cache.
export const dynamic = "force-dynamic";

export default async function Page() {
  const result = await loadSnapshot();

  if (!result.ok) {
    return (
      <main className="wrap">
        <Masthead />
        <div className="notice">
          <h3>No data to show yet</h3>
          <p>{result.reason}</p>
          <p>
            The dashboard reads <code>snapshots/latest.json</code> from{" "}
            <a href={`https://github.com/${REPO}`}>{REPO}</a>. That file is produced by the{" "}
            <b>Scheduled scan</b> GitHub Actions workflow.
          </p>
          <ul>
            <li>Open the repo&rsquo;s Actions tab</li>
            <li>
              Select <b>Scheduled scan</b> and click <b>Run workflow</b>
            </li>
            <li>Reload this page once it finishes</li>
          </ul>
        </div>
        <Footer source={result.source} />
      </main>
    );
  }

  const { snapshot } = result;
  const age = relativeAge(snapshot.generated_at);
  // The cron runs every 4 hours, so anything past ~5 is a missed run worth flagging.
  const freshness = age.hours > 12 ? "dead" : age.hours > 5 ? "stale" : "fresh";

  return (
    <main className="wrap">
      <Masthead>
        <div className="meta">
          <span className={`freshness ${freshness}`}>{age.text}</span>
          <span>{snapshot.duration_seconds}s build</span>
          {snapshot.quick_mode ? <span>quick mode</span> : null}
        </div>
      </Masthead>

      {freshness !== "fresh" ? (
        <div className="notice">
          <h3>This data is {age.text}</h3>
          <p>
            The scheduled scan runs every 4 hours. A gap this long usually means the workflow is
            failing or is disabled — check the repository&rsquo;s Actions tab.
          </p>
        </div>
      ) : null}

      {snapshot.errors?.length ? (
        <div className="notice">
          <h3>
            {snapshot.errors.length} section{snapshot.errors.length > 1 ? "s" : ""} failed this run
          </h3>
          {snapshot.errors.map((e) => (
            <p key={e.section}>
              <code>{e.section}</code> — {e.error}
            </p>
          ))}
          <p>Everything else below was collected normally.</p>
        </div>
      ) : null}

      <MarketOverview snapshot={snapshot} />
      <Watchlist rows={snapshot.watchlist ?? []} />
      <Scans snapshot={snapshot} />
      <Movers snapshot={snapshot} />
      <Prediction snapshot={snapshot} />
      <Backtests snapshot={snapshot} />

      <Footer source={result.source} generatedAt={snapshot.generated_at} />
    </main>
  );
}

function Masthead({ children }: { children?: React.ReactNode }) {
  return (
    <header className="masthead">
      <div>
        <h1>market-mcp</h1>
        <p>Crypto · IDX · global equities · prediction markets · backtests</p>
      </div>
      {children}
    </header>
  );
}

function Footer({ source, generatedAt }: { source: string; generatedAt?: string }) {
  return (
    <footer>
      <p>
        {generatedAt ? `Snapshot ${generatedAt}Z · ` : null}
        served from <a href={source}>latest.json</a> · source on{" "}
        <a href={`https://github.com/${REPO}`}>GitHub</a>
      </p>
      <p>
        Information only, not financial advice. Backtests are historical simulations and do not
        predict future returns.
      </p>
    </footer>
  );
}
