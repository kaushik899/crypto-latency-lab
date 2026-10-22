import { useEffect, useState } from "react";
import { CREDITS, ROOMS, paint } from "../lib/rooms";
import { onRoomChange, scrollToId } from "../lib/scroll";
import { Logo } from "./Logo";

const NUMERALS = ["I", "II", "III", "IV", "V"];

export function TopBar({ onTrade, onMenu }: { onTrade: () => void; onMenu: () => void }) {
  const [room, setRoom] = useState<string | null>(null);
  useEffect(() => onRoomChange(setRoom), []);
  const i = ROOMS.findIndex((r) => r.id === room);
  return (
    <header className="topbar">
      <a className="brand" href="#top" onClick={(e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }}
        aria-label="Likely Profits, back to the entrance">
        <Logo />
      </a>
      <p className="topbar-room" aria-live="polite">
        {i >= 0 ? <>Room {NUMERALS[i]} <span>·</span> {ROOMS[i].short}</> : "The entrance"}
      </p>
      <nav className="topbar-actions" aria-label="Site">
        <button className="pill gold" onClick={onTrade}>Trade now</button>
        <button className="pill" onClick={onMenu} aria-haspopup="dialog">Menu</button>
      </nav>
    </header>
  );
}

export function NextRoom() {
  const [room, setRoom] = useState<string | null>(null);
  useEffect(() => onRoomChange(setRoom), []);
  const i = ROOMS.findIndex((r) => r.id === room);
  const next = ROOMS[i + 1];
  if (!next) return null;
  return (
    <button className="next-card" onClick={() => scrollToId(next.id)} aria-label={`Next room: ${next.title}`}>
      <img src={`${paint(next.painting, true)}.webp`} alt="" loading="lazy" />
      <span className="next-text">
        <span className="next-no">Next · Room {NUMERALS[i + 1]}</span>
        <span className="next-title">{next.title}</span>
      </span>
    </button>
  );
}

export function Menu({ open, onClose, onTrade }: { open: boolean; onClose: () => void; onTrade: () => void }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  const go = (id: string) => { onClose(); setTimeout(() => scrollToId(id), 50); };
  return (
    <div className={`menu ${open ? "open" : ""}`} data-lenis-prevent role="dialog" aria-modal="true" aria-label="Rooms" aria-hidden={!open}>
      <div className="menu-head">
        <Logo />
        <button className="pill" onClick={onClose}>Close</button>
      </div>
      <ol className="menu-rooms">
        {ROOMS.map((r, i) => {
          const c = CREDITS[r.painting];
          return (
            <li key={r.id}>
              <button onClick={() => go(r.id)} tabIndex={open ? 0 : -1}>
                <img src={`${paint(r.painting, true)}.webp`} alt="" loading="lazy" />
                <span className="mr-no">Room {NUMERALS[i]}</span>
                <span className="mr-title">{r.title}</span>
                <span className="mr-credit">{c.artist}, <i>{c.title}</i></span>
              </button>
            </li>
          );
        })}
      </ol>
      <div className="menu-foot">
        <button className="btn gold" onClick={() => { onClose(); onTrade(); }} tabIndex={open ? 0 : -1}>Trade now</button>
        <a className="btn ghost" href="https://github.com/kaushik899/crypto-latency-lab" tabIndex={open ? 0 : -1}>Code on GitHub</a>
      </div>
    </div>
  );
}
