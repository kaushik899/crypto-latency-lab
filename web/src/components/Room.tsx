import type { ReactNode } from "react";
import { CREDITS, ROOMS, paint } from "../lib/rooms";

/** A gallery room: the painting fills the screen as an intro plate, then dims and stays behind the content. */
export function Room({ id, lede, children, focus = "50% 50%", continues = false }:
  { id: string; lede?: ReactNode; children: ReactNode; focus?: string; continues?: boolean }) {
  const idx = ROOMS.findIndex((r) => r.id === id);
  const room = ROOMS[idx];
  const c = CREDITS[room.painting];
  return (
    <section className={`room${continues ? " continues" : ""}`} id={id} data-room={id} aria-labelledby={`${id}-title`}>
      <div className="room-bg" aria-hidden>
        <picture>
          <source type="image/webp" srcSet={`${paint(room.painting, true)}.webp 1280w, ${paint(room.painting)}.webp 2000w`} sizes="100vw" />
          <img src={`${paint(room.painting)}.jpg`} alt="" loading="lazy" style={{ objectPosition: focus }} />
        </picture>
        <div className="room-shade" />
      </div>
      <header className={continues ? "room-compact" : "room-intro"}>
        <p className="room-no">Room {["I", "II", "III", "IV", "V"][idx]} · {room.short}</p>
        <h2 id={`${id}-title`} className="room-title">{room.title}</h2>
        {lede && <div className="room-lede">{lede}</div>}
        {!continues && <p className="plate">
          <span className="plate-artist">{c.artist}</span>
          <span><i>{c.title}</i>, {c.year}</span>
          <span className="plate-where">{c.where}</span>
        </p>}
      </header>
      <div className="room-body">{children}</div>
    </section>
  );
}
