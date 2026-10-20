// Same model as latlab/pricing.py and latlab/fees.py, ported for the browser.

export const SECONDS_PER_YEAR = 365 * 24 * 3600;
export const FEE_RATE = 0.07;
export const TICK = 0.01;

// Abramowitz–Stegun 7.1.26 erf, |error| < 1.5e-7
function erf(x: number) {
  const s = Math.sign(x);
  x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return s * y;
}

export const normCdf = (z: number) => 0.5 * (1 + erf(z / Math.SQRT2));

/** P(S_T >= K) for a cash-or-nothing digital with zero drift. */
export function digitalProb(spot: number, strike: number, tauSec: number, sigma: number) {
  if (tauSec <= 0) return spot >= strike ? 1 : 0;
  const sd = sigma * Math.sqrt(tauSec / SECONDS_PER_YEAR);
  return normCdf((Math.log(spot / strike) - 0.5 * sd * sd) / sd);
}

export const feePerShare = (p: number, rate = FEE_RATE) => rate * p * (1 - p);

export const roundBid = (x: number) => Math.min(1 - TICK, Math.max(TICK, Math.floor(x / TICK + 1e-9) * TICK));
export const roundAsk = (x: number) => Math.min(1 - TICK, Math.max(TICK, Math.ceil(x / TICK - 1e-9) * TICK));

export class EwmaVol {
  private varPerS: number;
  private lastT?: number;
  private lastP?: number;
  constructor(init = 0.5, private halflife = 300) {
    this.varPerS = (init * init) / SECONDS_PER_YEAR;
  }
  update(t: number, p: number) {
    if (this.lastT !== undefined && this.lastP && t > this.lastT) {
      const dt = t - this.lastT;
      const r = Math.log(p / this.lastP);
      const w = 1 - Math.pow(0.5, dt / this.halflife);
      this.varPerS = (1 - w) * this.varPerS + w * Math.min((r * r) / dt, 16 * this.varPerS); // winsorise jumps at 4 sd
    }
    this.lastT = t;
    this.lastP = p;
  }
  get sigma() {
    return Math.min(3, Math.max(0.1, Math.sqrt(this.varPerS * SECONDS_PER_YEAR)));
  }
}

// Box–Muller
export function randn() {
  let u = 0;
  while (u === 0) u = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random());
}
