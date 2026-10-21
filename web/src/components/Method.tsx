const REPO = "https://github.com/kaushik899/crypto-latency-lab";

export function Method() {
  return (
    <div className="method">

      <ol className="steps">
        <li>
          <h3>Stream real prices</h3>
          <p>Public order-book feeds from Binance, Coinbase, Kraken, OKX and Bybit for BTC, ETH, SOL, XRP, BNB and DOGE, sampled every 50 ms. No account or API key — read-only market data. Your clock is synced to Binance&rsquo;s so feed delays are measured fairly.</p>
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
          <li>Prices in &ldquo;Prices move on Binance first&rdquo; and &ldquo;Who moves first&rdquo; are real, streamed from public exchange feeds with no account. Gaps are measured against each pair&rsquo;s usual price difference, so a USD market and a USDT market are compared fairly; actually capturing a gap would also need money parked on both exchanges and is ignored here.</li>
          <li>The prediction-market odds come from a simulated market maker that reads the same live Binance prices after a delay. Real Polymarket books can be recorded by the Python engine where Polymarket is legally available.</li>
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
    </div>
  );
}
