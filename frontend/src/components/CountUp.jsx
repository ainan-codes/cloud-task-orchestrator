import React, { useEffect, useRef, useState } from "react";

/** Animates from its previous value to `value` over `ms` milliseconds. */
export default function CountUp({ value, ms = 600 }) {
  const [display, setDisplay] = useState(value);
  const prev = useRef(value);
  const frame = useRef(null);

  useEffect(() => {
    const from = prev.current;
    const to = value;
    prev.current = value;
    if (from === to) { setDisplay(to); return; }

    const start = performance.now();
    cancelAnimationFrame(frame.current);

    const tick = (now) => {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplay(Math.round(from + (to - from) * eased));
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [value, ms]);

  return <>{display}</>;
}
