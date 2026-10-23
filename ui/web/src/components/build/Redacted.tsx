const MARKER = /\[redacted:([A-Z0-9_]+)\]/g;

/** Text from the sandbox with every environment variable value already replaced by the server ([redacted:NAME]):
 *  each one shows as a locked chip naming the variable, so you see that something was hidden, and what. */
export function Redacted({ text }: { text: string }) {
  const parts: (string | { name: string })[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    parts.push(text.slice(last, match.index), { name: match[1] });
    last = (match.index ?? 0) + match[0].length;
  }
  parts.push(text.slice(last));
  return (
    <>
      {parts.map((part, index) =>
        typeof part === "string" ? (
          part
        ) : (
          <span key={index} className="redacted" title={`The value of ${part.name}, hidden`}>
            🔒{part.name}
          </span>
        ),
      )}
    </>
  );
}
