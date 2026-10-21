import credits from "../../public/paintings/credits.json";

export type PaintingId = "gallery" | "speed" | "babel" | "moneylender" | "icarus" | "astronomer";

export type Credit = { id: PaintingId; title: string; artist: string; year: string; where: string; source: string };

export const CREDITS = Object.fromEntries((credits as Credit[]).map((c) => [c.id, c])) as Record<PaintingId, Credit>;

const base = import.meta.env.BASE_URL;
export const paint = (id: PaintingId, small = false) => `${base}paintings/${id}${small ? "-sm" : ""}`;

export type RoomMeta = {
  id: string; // DOM id of the section
  painting: PaintingId;
  short: string; // name shown in the top bar and menu
  title: string; // the room's message, set in the display serif
};

export const ROOMS: RoomMeta[] = [
  { id: "live", painting: "speed", short: "Speed", title: "Watch the lag, live." },
  { id: "leadlag", painting: "babel", short: "Babel", title: "Five exchanges, one price, different clocks." },
  { id: "desk", painting: "moneylender", short: "The desk", title: "Weigh it, then trade it." },
  { id: "research", painting: "icarus", short: "Icarus", title: "Faster than the fee is not fast enough." },
  { id: "method", painting: "astronomer", short: "Method", title: "How every number here is measured." },
];

export const wallLabel = (c: Credit) => `${c.artist}, ${c.title}, ${c.year}`;
