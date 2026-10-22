// Buttery scrolling: Lenis interpolates wheel and keyboard scrolling on one rAF loop, so the
// painting dolly (lib/scroll.ts, which listens to native scroll events) moves without judder.
// Elements fade and rise into place as they enter the view. Both switch off with reduced motion.
import Lenis from "lenis";

let lenis: Lenis | null = null;

export const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function startSmooth() {
  if (lenis || reduced()) return;
  lenis = new Lenis({ lerp: 0.085, wheelMultiplier: 0.95, smoothWheel: true, syncTouch: false, anchors: true });
  const raf = (t: number) => { lenis?.raf(t); requestAnimationFrame(raf); };
  requestAnimationFrame(raf);
  document.documentElement.classList.add("smooth");
}

export function lockScroll(locked: boolean) {
  if (!lenis) return;
  if (locked) lenis.stop(); else lenis.start();
}

export function smoothTo(el: HTMLElement) {
  if (lenis) lenis.scrollTo(el, { offset: 0, duration: 1.4, easing: (t) => 1 - Math.pow(1 - t, 4) });
  else el.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
}

const REVEAL = [".block", ".room-lede", ".plate", ".setup", ".rates", ".plan", ".po", ".stk-acct", ".stk-track",
  ".stk-scan", ".stk-edge", ".auto-pub", ".auto-mine", ".strat-list li", ".auto-fig", ".rank"].join(",");

export function startReveal() {
  if (reduced() || !("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        const el = e.target as HTMLElement;
        el.classList.add("in");
        io.unobserve(el);
      }
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.06 });
  const mark = (root: ParentNode) => {
    root.querySelectorAll<HTMLElement>(REVEAL).forEach((el) => {
      if (el.classList.contains("rv")) return;
      // stagger siblings that arrive together
      const sibs = el.parentElement ? [...el.parentElement.children].filter((c) => c.matches(REVEAL)) : [];
      el.style.setProperty("--rv-delay", `${Math.min(6, Math.max(0, sibs.indexOf(el))) * 70}ms`);
      el.classList.add("rv");
      io.observe(el);
    });
  };
  mark(document);
  new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => {
    if (n instanceof HTMLElement) { if (n.matches(REVEAL)) mark(n.parentElement ?? document); mark(n); }
  }))).observe(document.body, { childList: true, subtree: true });
}
