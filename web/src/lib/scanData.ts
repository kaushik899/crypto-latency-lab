// The daily scan is ~700 KB: it is served as a static file and fetched once per page, never bundled.
// The copy deployed with the site arrives first; the repo copy replaces it if a newer scan landed.
import { useEffect, useState } from "react";

type AnyRec = Record<string, any>;
const LOCAL = `${import.meta.env.BASE_URL}data/stocks-scan.json`;
const RAW = "https://raw.githubusercontent.com/kaushik899/crypto-latency-lab/main/web/public/data/stocks-scan.json";

let current: AnyRec = { pending: true };
const subs = new Set<(d: AnyRec) => void>();
let started = false;

const newer = (a: AnyRec, b: AnyRec) => !b?.generated || (a?.generated ?? "") > b.generated;
function offer(d: AnyRec | null) {
  if (d && d.hits && newer(d, current)) {
    current = d;
    subs.forEach((f) => f(current));
  }
}
const get = (url: string) => fetch(url, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);

function start() {
  if (started) return;
  started = true;
  get(LOCAL).then(offer).then(() => get(`${RAW}?t=${Date.now()}`)).then(offer);
}

export function useScanData(): AnyRec {
  const [doc, setDoc] = useState<AnyRec>(current);
  useEffect(() => {
    start();
    subs.add(setDoc);
    if (current !== doc) setDoc(current);
    return () => { subs.delete(setDoc); };
  }, []);
  return doc;
}
