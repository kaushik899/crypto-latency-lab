import { Board } from "./components/Board";
import { LeadLag } from "./components/LeadLag";
import { PredictSim } from "./components/Live";
import { RealLive } from "./components/RealLive";
import { Method } from "./components/Method";
import { Research } from "./components/Research";

export default function App() {
  return (
    <>
      <a className="skip" href="#live">Skip to the live chart</a>
      <header className="top">
        <a className="brand" href="#live" aria-label="Crypto Latency Lab home">
          <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden>
            <path d="M4 21 C9 21 9 9 14 9 S19 21 24 21 28 15 28 15" fill="none" stroke="#5BC8F5" strokeWidth="2.6" strokeLinecap="round" />
            <path d="M4 23 H10 V12 H17 V23 H24 V17 H28" fill="none" stroke="#F2A93B" strokeWidth="2.2" strokeLinejoin="round" />
          </svg>
          Crypto Latency Lab
        </a>
        <nav aria-label="Sections">
          <a href="#live">Live</a>
          <a href="#leadlag">Who moves first</a>
          <a href="#predict">Simulation</a>
          <a href="#research">Backtest</a>
          <a href="#method">Method</a>
          <a href="https://github.com/kaushik899/crypto-latency-lab">GitHub</a>
        </nav>
      </header>
      <main>
        <RealLive />
        <LeadLag />
        <PredictSim />
        <Board />
        <Research />
        <Method />
      </main>
      <footer className="foot">
        <p>Built by Prashasth Kaushik Guda. Research and paper trading only — nothing here places orders or is investment advice.</p>
      </footer>
    </>
  );
}
