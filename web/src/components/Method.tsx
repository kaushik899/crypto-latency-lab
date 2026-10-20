const REPO = "https://github.com/kaushik899/crypto-latency-lab";

export function Method() {
  return (
    <section className="method" id="method" aria-labelledby="method-title">
      <header className="sec-head">
        <h2 id="method-title">How it works</h2>
        <p>One pipeline, used three ways: live in this page, live on your machine with real Polymarket books, and offline in the backtester.</p>
      </header>

      <ol className="steps">
        <li>
          <h3>Stream spot prices</h3>
          <p>Binance&rsquo;s public <code>bookTicker</code> stream for BTC, ETH, SOL, XRP, BNB and DOGE. No account or API key — it is read-only market data.</p>
        </li>
        <li>
          <h3>Price each market like an option</h3>
          <p>&ldquo;Up at 14:15&rdquo; pays $1 if the price finishes at or above where it opened: a cash-or-nothing digital call. Volatility is an exponentially weighted estimate from the last few minutes of ticks.</p>
          <pre className="formula">P(up) = Φ( [ ln(S / K) − σ²τ / 2 ] / σ√τ )</pre>
        </li>
        <li>
          <h3>Subtract the fee</h3>
          <p>Polymarket charges takers on crypto markets per share. A gap only counts if it beats this.</p>
          <pre className="formula">fee = 0.07 × p × (1 − p)        edge = fair − ask − fee</pre>
        </li>
        <li>
          <h3>Make the order wait</h3>
          <p>An order signalled now reaches the venue <em>latency</em> later. It fills only if the ask is still within a cent of where it was; otherwise it is recorded as a miss. This one rule is what separates paper profits from real ones.</p>
        </li>
        <li>
          <h3>Settle and score</h3>
          <p>Positions settle at window close. The backtester reports P&amp;L, hit rate, a t-statistic, drawdown, model calibration, and how long gaps survive — across latencies, fee regimes and competition levels.</p>
        </li>
      </ol>

      <div className="caveats">
        <h3>What this page is not</h3>
        <ul>
          <li>The odds on this page come from a simulated market maker that reads the same live Binance prices after a delay. Real Polymarket books are recorded and replayed by the Python engine on your own machine.</li>
          <li>Polymarket settles on Chainlink&rsquo;s price, not Binance&rsquo;s. The small difference between them is modelled in the backtester and is a real source of losses near the strike.</li>
          <li>Polymarket is blocked for French users since July 2026 and restricted elsewhere. This project never places orders anywhere.</li>
          <li>Simulated results depend on assumptions about how slow makers and rivals are. They show <em>which</em> assumptions matter, not what you would earn.</li>
        </ul>
      </div>

      <div className="run">
        <h3>Run it yourself</h3>
        <pre>{`git clone ${REPO}
cd crypto-latency-lab && pip install -r requirements.txt

python -m latlab.research                         # simulated study → data/research
python -m latlab.live --minutes 60 --record       # live monitor + paper trader
python -m latlab.tapes data/recordings/<run>      # recording → tape
python -m latlab.research --tape data/recordings/<run>/tape`}</pre>
        <a className="btn" href={REPO}>View the code on GitHub</a>
      </div>
    </section>
  );
}
