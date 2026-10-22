import { LogoMark } from "./Logo";
import { useEffect, useRef } from "react";
import { CREDITS, paint } from "../lib/rooms";
import { reducedMotion, scrollToId } from "../lib/scroll";

// Where the frame hangs inside Hubert Robert's Grande Galerie: just above the corridor's
// vanishing point (fractions of the painting's width and height).
const VP = { x: 0.345, y: 0.56 };
const GALLERY_ASPECT = 2000 / 1589;
const PAINTING_ASPECT = 2400 / 1792;

const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const ramp = (p: number, a: number, b: number) => clamp((p - a) / (b - a));

export function Hero({ onTrade }: { onTrade: () => void }) {
  const root = useRef<HTMLElement>(null);
  const scene = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = root.current!, sc = scene.current!, fr = frame.current!;
    const img = sc.querySelector<HTMLImageElement>(".hg-gallery")!;
    let raf = 0;
    let fx = 0, fy = 0, fw = 0, fh = 0, vw = 0, vh = 0;

    const layout = () => {
      vw = window.innerWidth;
      vh = window.innerHeight;
      // where the gallery image lands under object-fit: cover
      const ir = GALLERY_ASPECT, vr = vw / vh;
      const dw = vr > ir ? vw : vh * ir;
      const dh = vr > ir ? vw / ir : vh;
      // pan the cropped painting so the vanishing point stays on screen on narrow, tall viewports
      const ox = Math.min(0, Math.max(vw - dw, vw * 0.5 - VP.x * dw)), oy = (vh - dh) / 2;
      img.style.objectPosition = `${dw > vw ? (ox / (vw - dw)) * 100 : 50}% 50%`;
      fx = ox + VP.x * dw;
      fy = oy + VP.y * dh;
      fw = Math.max(130, Math.min(vw * 0.15, 300));
      fh = fw / PAINTING_ASPECT;
      Object.assign(fr.style, { left: `${fx - fw / 2}px`, top: `${fy - fh / 2}px`, width: `${fw}px`, height: `${fh}px` });
      sc.style.transformOrigin = `${fx}px ${fy}px`;
      draw();
    };

    const draw = () => {
      raf = 0;
      const r = el.getBoundingClientRect();
      const total = el.offsetHeight - vh;
      const p = reducedMotion() ? 1 : clamp(-r.top / total);
      const z = ease(ramp(p, 0.08, 0.72));
      // scale until the painting (frame inner) covers the viewport, in log space so it feels like a dolly
      const target = Math.max(vw / (fw * 0.86), vh / (fh * 0.84)) * 1.04;
      const s = Math.pow(target, z);
      sc.style.transform = `translate(${(vw / 2 - fx) * z}px, ${(vh / 2 - fy) * z}px) scale(${s})`;
      el.style.setProperty("--intro", String(1 - ramp(p, 0.02, 0.16)));
      el.style.setProperty("--gallery", String(1 - ramp(p, 0.5, 0.66)));
      el.style.setProperty("--full", String(ramp(p, 0.46, 0.64)));
      el.style.setProperty("--title", String(ramp(p, 0.7, 0.86)));
      el.style.setProperty("--fullscale", String(1.12 - 0.12 * ramp(p, 0.46, 1)));
    };

    const onScroll = () => { if (!raf) raf = requestAnimationFrame(draw); };
    layout();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", layout);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", layout);
      cancelAnimationFrame(raf);
    };
  }, []);

  const c = CREDITS.speed, g = CREDITS.gallery;
  return (
    <section ref={root} className="hero-gallery" aria-labelledby="hero-title">
      <div className="hg-stage">
        <div ref={scene} className="hg-scene">
          <picture>
            <source type="image/webp" srcSet={`${paint("gallery", true)}.webp 1280w, ${paint("gallery")}.webp 2000w`} sizes="100vw" />
            <img className="hg-gallery" src={`${paint("gallery")}.jpg`} alt="" fetchPriority="high" />
          </picture>
          <div className="hg-dim" />
          <div ref={frame} className="hg-frame">
            <span className="hg-wire l" />
            <span className="hg-wire r" />
            <span className="hg-light" />
            <div className="gilt">
              <img src={`${paint("speed")}.webp`} alt="" decoding="async" />
            </div>
          </div>
        </div>

        <picture className="hg-full" aria-hidden>
          <source type="image/webp" srcSet={`${paint("speed", true)}.webp 1280w, ${paint("speed")}.webp 2000w`} sizes="100vw" />
          <img src={`${paint("speed")}.jpg`} alt="" />
        </picture>
        <div className="hg-full-shade" />

        <div className="hg-intro">
          <p className="hg-kicker">The odds of every trade, measured · Crypto &amp; US stocks</p>
          <p className="hg-big"><LogoMark size={84} id="hero" /><span className="hg-word">Likely<br />Profits</span></p>
          <p className="hg-scroll">Scroll to enter the gallery</p>
          <p className="hg-plate">{g.artist}, <i>{g.title}</i>, {g.year}</p>
        </div>

        <div className="hg-title">
          <h1 id="hero-title">Prices move on Binance first.<br />Everyone else catches up.</h1>
          <p className="hg-lede">
            A live lab that streams five crypto exchanges, times who follows whom, and checks whether the gaps
            survive real fees. Turner painted the same race in 1844: a hare running for its life ahead of a steam train.
          </p>
          <div className="hg-actions">
            <button className="btn gold" onClick={() => scrollToId("live")}>Watch the lag live</button>
            <button className="btn ghost" onClick={onTrade}>Trade now</button>
          </div>
          <p className="hg-plate on-paint">{c.artist}, <i>{c.title}</i>, {c.year} · {c.where}</p>
        </div>
      </div>
    </section>
  );
}
