import { useEffect, useState } from "react";
import { lab } from "./lab";

/** Re-render at most every ``ms`` while the lab streams. */
export function useLab(ms = 250) {
  const [, set] = useState(0);
  useEffect(() => {
    lab.start();
    let last = 0;
    let pending: number | undefined;
    const off = lab.subscribe(() => {
      const now = performance.now();
      if (now - last >= ms) {
        last = now;
        set(lab.version);
      } else if (!pending) {
        pending = window.setTimeout(() => {
          pending = undefined;
          last = performance.now();
          set(lab.version);
        }, ms - (now - last));
      }
    });
    return () => {
      off();
      if (pending) window.clearTimeout(pending);
    };
  }, [ms]);
  return lab;
}
