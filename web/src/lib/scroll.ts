import { smoothTo } from "./smooth";
// One rAF-driven scroll loop for the whole page. Rooms get --p (0→1 progress through the room)
// and --shade (how much the painting is dimmed behind the content); listeners learn which room
// is current. Everything is CSS-variable driven so the paintings never re-render React.

type Listener = (roomId: string | null) => void;

const listeners = new Set<Listener>();
let current: string | null = null;
let ticking = false;
let started = false;

export const reducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function update() {
  ticking = false;
  const vh = window.innerHeight;
  let found: string | null = null;
  let plate = window.scrollY < vh * 0.5;
  document.querySelectorAll<HTMLElement>("[data-room]").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.bottom < -vh || r.top > 2 * vh) return;
    const p = Math.min(1, Math.max(0, (vh - r.top) / (r.height + vh)));
    // painting is bright while the intro plate is on screen, then dims behind the content
    const into = Math.min(1, Math.max(0, -r.top / (vh * 0.7)));
    el.style.setProperty("--p", p.toFixed(4));
    el.style.setProperty("--shade", (0.18 + 0.66 * into).toFixed(3));
    if (r.top <= vh * 0.4 && r.bottom > vh * 0.4) found = el.dataset.room ?? null;
    if (!el.classList.contains("continues") && r.top < vh * 0.5 && r.top > -vh * 0.35) plate = true;
  });
  document.documentElement.toggleAttribute("data-plate", plate);
  if (found !== current) {
    current = found;
    listeners.forEach((f) => f(current));
  }
}

const onScroll = () => {
  if (!ticking) {
    ticking = true;
    requestAnimationFrame(update);
  }
};

export function startScroll() {
  if (started) return;
  started = true;
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  update();
}

export function onRoomChange(f: Listener) {
  listeners.add(f);
  f(current);
  return () => { listeners.delete(f); };
}

export function scrollToId(id: string) {
  const el = document.getElementById(id);
  if (el) smoothTo(el);
}
