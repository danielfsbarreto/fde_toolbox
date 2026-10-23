import { useEffect, useRef, useState } from "react";

interface Props {
  value: string;
  onSave: (title: string) => Promise<boolean>;
  maxLength?: number;
}

/** A title edited in place: click it to edit; Enter or clicking away saves, Escape cancels. */
export function EditableTitle({ value, onSave, maxLength = 120 }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);
  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  const save = async () => {
    const title = draft.trim();
    if (!title || title === value) {
      setEditing(false);
      setDraft(value);
      return;
    }
    setSaving(true);
    const saved = await onSave(title);
    setSaving(false);
    if (saved) setEditing(false);
  };

  if (!editing) {
    return (
      <button className="editable-title" onClick={() => setEditing(true)} title="Rename">
        <span className="editable-title__text">{value}</span>
        <span className="editable-title__pen" aria-hidden>
          ✎
        </span>
      </button>
    );
  }
  return (
    <input
      ref={input}
      className="field editable-title__input"
      value={draft}
      maxLength={maxLength}
      disabled={saving}
      aria-label="Title"
      onChange={(event) => setDraft(event.target.value)}
      onBlur={save}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          save();
        } else if (event.key === "Escape") {
          setDraft(value);
          setEditing(false);
        }
      }}
    />
  );
}
