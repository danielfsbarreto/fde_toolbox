import { useEffect, useRef, useState } from "react";

interface Props {
  value: string;
  onSave: (value: string) => void;
  placeholder?: string;
  multiline?: boolean;
  className?: string;
  disabled?: boolean;
  ariaLabel?: string;
}

/** Text that reads like text and edits in place; saves on blur or Enter (Shift+Enter for a new line). */
export function Inline({ value, onSave, placeholder, multiline, className = "", disabled, ariaLabel }: Props) {
  const [draft, setDraft] = useState(value);
  const area = useRef<HTMLTextAreaElement>(null);
  const cancelled = useRef(false);
  useEffect(() => setDraft(value), [value]);
  useEffect(() => {
    const element = area.current;
    if (element) {
      // Grow with the text; the borders are outside scrollHeight, so add them or the field scrolls.
      element.style.height = "auto";
      element.style.height = `${element.scrollHeight + element.offsetHeight - element.clientHeight}px`;
    }
  }, [draft]);

  const commit = () => {
    if (cancelled.current) {
      cancelled.current = false; // Escape: leave without saving
      return;
    }
    if (draft.trim() !== value.trim()) onSave(draft.trim());
  };
  const keys = (event: React.KeyboardEvent) => {
    if (event.key === "Enter" && !(multiline && event.shiftKey)) {
      event.preventDefault();
      (event.target as HTMLElement).blur();
    }
    if (event.key === "Escape") {
      cancelled.current = true;
      setDraft(value);
      (event.target as HTMLElement).blur();
    }
  };
  const common = {
    className: `inline ${className}`,
    value: draft,
    placeholder,
    disabled,
    "aria-label": ariaLabel ?? placeholder,
    onBlur: commit,
    onKeyDown: keys,
  };
  return multiline ? (
    <textarea ref={area} rows={1} {...common} onChange={(event) => setDraft(event.target.value)} />
  ) : (
    <input {...common} onChange={(event) => setDraft(event.target.value)} />
  );
}
