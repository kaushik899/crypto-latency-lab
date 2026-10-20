import { useEffect, useState } from "react";
import { real } from "./real";

/** Re-render at most every ``ms`` while the real-data lab streams. */
export function useReal(ms = 300) {
  const [, set] = useState(0);
  useEffect(() => {
    real.start();
    let last = 0;
    let pending: number | undefined;
    const off = real.subscribe(() => {
      const now = performance.now();
      if (now - last >= ms) {
        last = now;
        set(real.version);
      } else if (!pending) {
        pending = window.setTimeout(() => {
          pending = undefined;
          last = performance.now();
          set(real.version);
        }, ms - (now - last));
      }
    });
    return () => {
      off();
      if (pending) window.clearTimeout(pending);
    };
  }, [ms]);
  return real;
}
