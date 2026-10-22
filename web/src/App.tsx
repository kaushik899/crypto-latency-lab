import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { Menu, NextRoom, TopBar } from "./components/Chrome";
import { AutoTrader } from "./components/AutoTrader";
import { Board } from "./components/Board";
import { Hero } from "./components/Hero";
import { LeadLag } from "./components/LeadLag";
import { PredictSim } from "./components/Live";
import { Method } from "./components/Method";
import { RealLive } from "./components/RealLive";
import { Room } from "./components/Room";
import { TradeDrawer } from "./components/TradeDrawer";
import { CREDITS } from "./lib/rooms";

// Below-the-fold rooms load in their own chunks, so the first screen paints sooner.
const Stocks = lazy(() => import("./components/Stocks").then((m) => ({ default: m.Stocks })));
const Research = lazy(() => import("./components/Research").then((m) => ({ default: m.Research })));
const Loading = () => <div className="block loading" aria-busy="true"><span className="shimmer" /></div>;
import { scrollToId, startScroll } from "./lib/scroll";
import { deskStatus, setDesk } from "./lib/trade";
import { lockScroll, startReveal, startSmooth } from "./lib/smooth";

export default function App() {
  const [trade, setTrade] = useState(false);
  const [tab, setTab] = useState<"practice" | "binance" | undefined>(undefined);
  const [menu, setMenu] = useState(false);
  const openTrade = useCallback(() => { setTab(undefined); setTrade(true); }, []);
  const openConnect = useCallback(() => { setTab("binance"); setTrade(true); }, []);
  const closeTrade = useCallback(() => setTrade(false), []);
  const closeMenu = useCallback(() => setMenu(false), []);
  useEffect(() => { startSmooth(); startScroll(); startReveal(); }, []);
  // The desk opens this page as …#pair=123456: pair at once and show the accounts, then drop the code
  // from the address bar (a URL fragment is never sent to any server).
  useEffect(() => {
    const m = location.hash.match(/pair=(\d{6})/);
    if (!m) return;
    history.replaceState(null, "", location.pathname + location.search);
    deskStatus(m[1]).then((st) => { setDesk(m[1], st); setTab("binance"); setTrade(true); }).catch(() => { setTab("binance"); setTrade(true); });
  }, []);
  useEffect(() => {
    document.body.style.overflow = trade || menu ? "hidden" : "";
    lockScroll(trade || menu);
  }, [trade, menu]);

  return (
    <>
      <a className="skip" href="#live">Skip to the live chart</a>
      <TopBar onTrade={openTrade} onMenu={() => setMenu(true)} />
      <main id="top">
        <Hero onTrade={openTrade} />

        <Room id="live" continues>
          <RealLive />
        </Room>

        <Room id="leadlag" focus="55% 40%" lede={<p>
          When Binance moves sharply, the clock starts. Each exchange&rsquo;s time is how long it takes to cover half the
          move, measured from your own connection, so it is what a trader sitting where you are would see.
        </p>}>
          <LeadLag />
        </Room>

        <Room id="desk" focus="50% 40%" lede={<>
          <p>
            Trade in one click, or let the auto-trader watch every market for you. Both run through a trade desk on your
            own computer, so your exchange keys never leave it, and real money is only ever risked by strategies whose
            paper record has proved they beat the fees.
          </p>
          <div className="hg-actions">
            <button className="btn gold" onClick={openTrade}>Trade now</button>
            <button className="btn ghost" onClick={() => scrollToId("auto")}>See the auto-trader</button>
          </div>
        </>}>
          <AutoTrader onConnect={openConnect} />
          <PredictSim />
          <Board />
        </Room>

        <Room id="stocks" focus="55% 22%" lede={<p>
          Not only crypto: the same discipline on US stocks. Rules are tested walk-forward on the most liquid names with
          real costs, today&rsquo;s breakouts are posted with a plan and a size for a small account, and every order waits
          for your own approval through the desk.
        </p>}>
          <Suspense fallback={<Loading />}><Stocks onConnect={openConnect} /></Suspense>
        </Room>

        <Room id="research" focus="60% 60%" lede={<p>
          The Python backtester replays 612 simulated prediction markets per scenario across four random days. In a slow,
          fee-free market almost any bot profits. Add the taker fee and fast rivals, and only near-instant orders survive.
        </p>}>
          <Suspense fallback={<Loading />}><Research /></Suspense>
        </Room>

        <Room id="method" focus="70% 40%" lede={<p>
          The live rooms run on real exchange data in your browser. The prediction-market rooms use the same model on a
          simulated market, because Polymarket is blocked in France.
        </p>}>
          <Method />
        </Room>
      </main>

      <footer className="foot">
        <div className="foot-row">
          <p>Built by Prashasth Kaushik Guda. Research and paper trading; orders are only ever placed by your own trade desk.
            Not investment advice.</p>
          <button className="link" onClick={() => scrollToId("method")}>How it works</button>
        </div>
        <p className="foot-credits">
          Paintings, all public domain, from Wikimedia Commons:{" "}
          {Object.values(CREDITS).map((c, i, a) => (
            <span key={c.id}>
              <a href={c.source}>{c.artist}, <i>{c.title}</i> ({c.year})</a>{i < a.length - 1 ? "; " : "."}
            </span>
          ))}
        </p>
      </footer>

      <NextRoom />
      <Menu open={menu} onClose={closeMenu} onTrade={openTrade} />
      <TradeDrawer open={trade} onClose={closeTrade} tab={tab} />
    </>
  );
}
