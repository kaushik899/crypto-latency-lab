// Downloads the public-domain paintings used on the site from Wikimedia Commons.
// Run by .github/workflows/paintings.yml (the build container has no route to Wikimedia).
import { mkdir, writeFile } from "node:fs/promises";

const UA = "CryptoLatencyLab/1.0 (https://github.com/kaushik899/crypto-latency-lab)";
const OUT = new URL("../public/paintings/", import.meta.url);

const WORKS = [
  { id: "gallery", file: "Hubert Robert - Projet d'aménagement de la Grande Galerie du Louvre (1796).JPG",
    title: "Project for the Grande Galerie of the Louvre", artist: "Hubert Robert", year: "1796", where: "Musée du Louvre, Paris" },
  { id: "speed", file: "Turner - Rain, Steam and Speed - National Gallery file.jpg",
    title: "Rain, Steam and Speed – The Great Western Railway", artist: "J. M. W. Turner", year: "1844", where: "National Gallery, London" },
  { id: "babel", file: "Pieter Bruegel the Elder - The Tower of Babel (Vienna) - Google Art Project - edited.jpg",
    title: "The Tower of Babel", artist: "Pieter Bruegel the Elder", year: "1563", where: "Kunsthistorisches Museum, Vienna" },
  { id: "moneylender", file: "The Moneylender and his Wife by Quinten Massijs.jpg",
    title: "The Moneylender and his Wife", artist: "Quentin Matsys", year: "1514", where: "Musée du Louvre, Paris" },
  { id: "icarus", file: "Pieter Bruegel the Elder - Landscape with the Fall of Icarus - Brussels, Royal Museums of Fine Arts of Belgium - Google Arts & Culture.jpg",
    title: "Landscape with the Fall of Icarus", artist: "after Pieter Bruegel the Elder", year: "c. 1560", where: "Royal Museums of Fine Arts, Brussels" },
  { id: "astronomer", file: "Johannes Vermeer - The Astronomer - 1668.jpg",
    title: "The Astronomer", artist: "Johannes Vermeer", year: "1668", where: "Musée du Louvre, Paris" },
];

async function info(file, width) {
  const u = new URL("https://commons.wikimedia.org/w/api.php");
  u.search = new URLSearchParams({ action: "query", format: "json", prop: "imageinfo", iiprop: "url|size",
    iiurlwidth: String(width), titles: `File:${file}` }).toString();
  const j = await (await fetch(u, { headers: { "User-Agent": UA } })).json();
  const p = Object.values(j.query.pages)[0];
  if (!p.imageinfo) throw new Error(`missing on Commons: ${file}`);
  return { ...p.imageinfo[0], page: p.imageinfo[0].descriptionurl };
}

await mkdir(OUT, { recursive: true });
const credits = [];
for (const w of WORKS) {
  for (const [suffix, width] of [["", 2400], ["-sm", 1100]]) {
    const ii = await info(w.file, width);
    const r = await fetch(ii.thumburl, { headers: { "User-Agent": UA } });
    if (!r.ok) throw new Error(`${r.status} for ${ii.thumburl}`);
    const buf = Buffer.from(await r.arrayBuffer());
    await writeFile(new URL(`${w.id}${suffix}.jpg`, OUT), buf);
    console.log(`${w.id}${suffix}.jpg  ${(buf.length / 1024).toFixed(0)} KB`);
    if (!suffix) credits.push({ ...w, width: ii.thumbwidth, height: ii.thumbheight, source: ii.page, license: "Public domain" });
    await new Promise((res) => setTimeout(res, 800));
  }
}
await writeFile(new URL("credits.json", OUT), JSON.stringify(credits, null, 1));
