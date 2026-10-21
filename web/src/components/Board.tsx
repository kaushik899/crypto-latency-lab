import { ASSETS, fmtPrice } from "../lib/assets";
import { useLab } from "../lib/useLab";

export function Board() {
  const lab = useLab(500);
  const now = Date.now() / 1000;
  const rows = [...lab.markets.values()].sort(
    (a, b) => ASSETS.findIndex((x) => x.key === a.asset) - ASSETS.findIndex((x) => x.key === b.asset) || a.end - b.end,
  );
  return (
    <div className="board block" id="board">
      <header className="block-head">
        <h3>Every market at once</h3>
        <p>
          {rows.length} markets priced ten times a second. Edge is what buying the cheaper side would earn per share,
          on average, after Polymarket&rsquo;s taker fee. Lit rows are the moments a fast trader would act.
        </p>
      </header>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Market</th>
              <th scope="col">Strike</th>
              <th scope="col">Spot</th>
              <th scope="col" className="r">Fair</th>
              <th scope="col" className="r">Bid</th>
              <th scope="col" className="r">Ask</th>
              <th scope="col" className="r">Best edge</th>
              <th scope="col">Time left</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => {
              const edge = Math.max(m.edgeYes ?? -1, m.edgeNo ?? -1);
              const side = (m.edgeYes ?? -1) >= (m.edgeNo ?? -1) ? "Up" : "Down";
              const life = Math.max(0, (m.end - now) / (m.end - m.start));
              const hot = edge > lab.entryEdge;
              return (
                <tr key={m.id} className={hot ? "hot" : ""}>
                  <th scope="row">{m.asset} <span className="muted">{m.window}</span></th>
                  <td className="num">{fmtPrice(m.strike)}</td>
                  <td className="num">{fmtPrice(lab.px[m.asset])}</td>
                  <td className="num r">{m.fair !== undefined ? (m.fair * 100).toFixed(1) : "—"}</td>
                  <td className="num r">{(m.bid * 100).toFixed(0)}</td>
                  <td className="num r">{(m.ask * 100).toFixed(0)}</td>
                  <td className={`num r ${edge > 0 ? "pos" : "neg"}`}>
                    {m.fair !== undefined ? `${edge >= 0 ? "+" : "−"}${Math.abs(edge * 100).toFixed(1)}¢ ${side}` : "—"}
                  </td>
                  <td>
                    <span className="life" aria-label={`${Math.ceil(m.end - now)} seconds left`}>
                      <span style={{ transform: `scaleX(${life})` }} />
                    </span>
                    <span className="num muted">{Math.ceil(m.end - now)}s</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
