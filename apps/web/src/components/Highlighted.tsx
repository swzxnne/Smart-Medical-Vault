// Must match HIGHLIGHT_START / HIGHLIGHT_STOP in the API's retrieval module.
const START = "⟦";
const STOP = "⟧";

/** Renders a search snippet, bolding the ⟦matched⟧ terms without using innerHTML. */
export function Highlighted({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest) {
    const start = rest.indexOf(START);
    if (start === -1) {
      parts.push(rest);
      break;
    }
    const stop = rest.indexOf(STOP, start);
    if (stop === -1) {
      parts.push(rest.replace(START, ""));
      break;
    }
    parts.push(rest.slice(0, start));
    parts.push(
      <mark key={key++} className="rounded bg-amber-100 px-0.5 text-slate-900">
        {rest.slice(start + 1, stop)}
      </mark>,
    );
    rest = rest.slice(stop + 1);
  }
  return <>{parts}</>;
}
