# Crypto Latency Lab

Viral posts claim bots get rich because crypto prices move on Binance first and everywhere else a
moment later. This project measures that claim on **real, live data**: it streams order books from
Binance, Coinbase, Kraken, OKX and Bybit, times how long each exchange takes to follow a move on
Binance, and checks whether the gaps in between survive real trading fees and real latency. A second
part asks the same question for Polymarket-style "up or down in 5 minutes" markets, using an
option-pricing model and a simulator (Polymarket is blocked in France).

**Live site:** https://kaushik899.github.io/crypto-latency-lab/

> Research and paper trading only. Nothing in this repository signs, sends or places orders.
> No exchange account or API key is needed or used.

![The gallery entrance: a camera dolly into Turner's Rain, Steam and Speed](docs/hero.png)

The site is a museum of market timing. Each room hangs a public-domain painting chosen for its idea —
Turner's *Rain, Steam and Speed* for latency, Bruegel's *Tower of Babel* for five exchanges quoting one price,
Matsys' *Moneylender and his Wife* for the trade desk, *The Fall of Icarus* for the backtest, Vermeer's
*Astronomer* for the method — and scrolling dollies the camera from Hubert Robert's Grande Galerie of the
Louvre into the first frame.

## Trade now (one click, your own Binance account)

The site has a **Trade now** button. *Practice* fills at the live Binance price with a realistic order
delay and fee, no account needed. *My Binance* places real market orders in about a fifth of a second
through a small **trade desk** that runs on your computer:

```bash
# 1. free practice keys: https://testnet.binance.vision (log in with GitHub)
export BINANCE_TESTNET_KEY=...  BINANCE_TESTNET_SECRET=...
python -m latlab.desk                     # prints a six-digit pairing code

# 2. real money, when you are ready (API key: Spot trading ON, withdrawals OFF)
export BINANCE_API_KEY=...  BINANCE_API_SECRET=...
python -m latlab.desk --live --max-order 25
```

Then open the site, click **Trade now → My Binance**, and enter the code. Why a local desk: Binance
refuses signed requests from web pages (CORS), and a public website must never hold a secret key. The
desk keeps the key on your machine, listens on 127.0.0.1 only, answers only this site and only with the
pairing code, caps each order (`--max-order`), allows one order per second, keeps one warm HTTPS
connection to Binance so orders skip the TLS handshake, and logs every order to `data/trades/`.
Live orders always need a second click within four seconds.

## Real data: recorded session

20.8 minutes of live quotes captured from Paris on 20 October 2026 (`data/research/real-session.json`),
all six coins, measured in the site's real-data mode with the clock synced to Binance.

| Exchange | Feed delay to Paris (median) | Follows a Binance move in (median) | Moves timed | Taker fee |
|---|---|---|---|---|
| Binance | 155 ms | leader | — | 0.10% |
| Coinbase | 53 ms | 350 ms | 105 | 0.50% |
| Kraken | 21 ms | 300 ms | 115 | 0.80% |
| OKX | 125 ms | 300 ms | 112 | 0.10% |
| Bybit | 97 ms | 350 ms | 114 | 0.10% |

Binance does lead: when it moves, the other four take roughly 300–350 ms to cover half the move, even
though Binance's own feed reaches Paris *slower* than theirs. But in the whole session, **not one**
cross-exchange gap was larger than the two taker fees needed to trade it (0.20% for the cheapest pair),
so latency-checked paper P&L was **$0**. The lag is real; at retail fees it is not tradable. The
viral "price gap" profits need maker rebates or VIP fee tiers, co-location, and capital pre-positioned
on every exchange.

## Simulation: Polymarket-style markets (4 random days × 6 h × 612 markets per scenario)

| Scenario | P&L at 150 ms on $10k | Break-even latency | Half of gaps gone within |
|---|---|---|---|
| No fee, slow rivals (~400 ms) | **+$7,729** (t = 3.7) | ~930 ms | ~120 ms |
| Taker fee, slow rivals | **+$5,950** (t = 3.0), $2,835 paid in fees | ~740 ms | ~90 ms |
| Taker fee, crowded (rivals ~20 ms, makers re-quote every 0.3 s) | **−$1,513** (t = −1.8) | **~39 ms** | ~30 ms |

The edge the posts describe is real in a slow, fee-free market. Polymarket's crypto taker fee
(`0.07 × p × (1 − p)` per share, 1.75¢ at 50¢) removes about a quarter of it. Fast competition removes
the rest: once rivals react in tens of milliseconds, the orders that still fill are the ones the market
was right about. These numbers come from a simulator whose assumptions are explicit parameters
(`latlab/sim.py`); they show which assumptions the profit depends on, not what anyone would earn.

## Auto-trader

```bash
python -m latlab.connect                          # connect Binance / OKX / Bybit / Kraken / Coinbase (once)
python -m latlab.bot run                          # paper trading on live data, top 30 markets, no keys
python -m latlab.bot run --mode testnet           # real orders on exchange testnets (fake money)
python -m latlab.bot run --mode live --i-understand-real-money
python -m latlab.bot status | kill | resume
```

Or run `python -m latlab.desk` (add `--live --auto` for real money) and control it from the website's
**Auto-trader** panel: start, stop, kill switch, order size, daily loss cap.

How it decides, in order:

1. **Three strategies** produce a signal per market: *lead-lag* (Binance moved, a slower exchange hasn't),
   *order-book imbalance* (much more size on the bid than the ask), *dip reversion* (sharp drop below the
   5-minute average). All long-only, because spot accounts can't short.
2. **Online calibration.** Every signal is checked against what the price actually did over the
   strategy's horizon, measured from when an order would really have arrived. Per strategy and coin it
   fits `future move = beta x signal` with exponential forgetting; a model may predict only once beta is
   positive with t >= 3.
3. **Cost filter.** It trades only if the prediction *minus two standard errors* beats two taker fees,
   the bid-ask spread and a buffer.
4. **Risk limits.** Order size, positions open at once, one position per coin, trades per hour, stale
   price and wide spread guards, a daily loss cap (halts until the next UTC day) and a kill switch file.
5. **Performance gate.** Real money is unlocked per strategy only after 100+ paper trades in the last
   7 days are profitable after costs with t >= 2. Until then every signal is paper-traded, so the record
   keeps building.

Connecting an account tests the key and **refuses keys that can withdraw** (checked automatically on
Binance, OKX, Bybit and Coinbase). Keys stay in `~/.latlab/credentials.json` (owner-only) or the system
keychain.

It does not promise profit. On a synthetic market with a real 0.6 s lag it finds the lag and profits
(about 480 trades, t of 6 to 9) when costs are zero, and correctly stays out when 0.10% fees make the
lag unprofitable.

## Self-updating research

`.github/workflows/research.yml` runs every four hours: it records 20 minutes of live quotes (30
Binance markets via its public data mirror, plus Coinbase, Kraken, OKX and Bybit), measures who follows
Binance, replays every auto-trader strategy on the recording in paper mode (continuing the calibration
from previous sessions in `data/bot/models.json`) and commits the result, which redeploys the site.

## What's inside

```
latlab/
  pricing.py     digital-option fair value (normal, Student-t, Merton jump-diffusion), jump-robust EWMA vol
  fees.py        Polymarket taker fee model
  feeds/         five exchange websockets (exchanges.py) · Binance · Polymarket Gamma + CLOB order books
  leadlag.py     record real quotes from five exchanges; catch-up times, lead-lag, fee-aware gaps
  desk.py        local trade desk behind Trade now and the Auto-trader panel (testnet default)
  connect.py     one-command account connection with key and withdrawal-permission checks
  universe.py    top Binance markets by volume, minus stablecoins and tokenised stocks
  bot/           auto-trader: strategies, online models, risk, ledger + gate, paper/ccxt brokers, runner
  tape.py        time-aligned record of spot + books (from simulation or from a live recording)
  sim.py         correlated GBM + stochastic vol + jumps, oracle basis, lagging market maker, rival latency bots
  strategy.py    fee-aware signals, latency-delayed paper execution, Kelly sizing, global capital ledger
  metrics.py     P&L, hit rate, t-stat, Sharpe, drawdown, Brier score, calibration
  research.py    the study: latency sweeps over seeds, fee/competition regimes, gap lifetimes, model risk
  live.py        live monitor + paper trader + recorder (all six coins)
  tapes.py       recording → tape for the backtester
web/             React + TypeScript gallery site: live rooms, Trade now drawer, research charts, method
tests/           43 tests (pricing, fees, order books, simulator invariants, capital limits, P&L accounting)
```

## Assets

BTC, ETH, SOL, XRP, BNB and DOGE, all priced from Binance spot. Polymarket lists short-dated Up/Down
markets for BTC, ETH, SOL and XRP; BNB and DOGE are covered by the live fair-value monitor and the simulator.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q

python -m latlab.leadlag record --minutes 30                # real quotes from five exchanges
python -m latlab.leadlag analyze data/recordings/leadlag-<time>

python -m latlab.research                                   # simulated study (≈8 min) → data/research/results.json
python -m latlab.live --minutes 60 --record                 # live monitor + paper trader + recorder
python -m latlab.tapes data/recordings/<run>                # build a tape from the recording
python -m latlab.research --tape data/recordings/<run>/tape # same study on real data

cd web && npm install && npm run dev                        # website
```

## Method in one paragraph

An Up/Down contract pays $1 if the oracle price at the close is at or above the open: a cash-or-nothing
digital, worth `Φ([ln(S/K) − σ²τ/2] / σ√τ)` with zero drift. σ is a jump-robust exponentially weighted
estimate from tick data. A signal fires when `fair − ask − fee` exceeds 1.5¢. The order then arrives
`latency` later and fills only at the book that exists at that moment, within one tick of the signal
price, capped by displayed size and a global capital budget. Positions settle on the oracle price.

## Limitations

- Polymarket settles on Chainlink's price; the live tools use Binance as a proxy. The simulator models the
  basis between them.
- Simulated makers and rivals are stylised. Real books have queue priority, partial fills and makers who
  widen spreads when they are picked off.
- Polymarket is blocked for users in France (ANJ order, July 2026) and restricted elsewhere. Check what
  applies where you are before collecting data.

## Author

Prashasth Kaushik Guda — MSc in Management, SKEMA Business School (Paris).

## Credits

Paintings are public domain, fetched from Wikimedia Commons by `web/scripts/fetch-paintings.mjs`
(see `web/public/paintings/credits.json` for sources). Type: Instrument Serif and Schibsted Grotesk.
