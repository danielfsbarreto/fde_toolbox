import { useState } from "react";
import type { ManualEntry } from "../types";
import { WORKSTREAMS } from "../types";

interface Props {
  accounts: Record<string, string>;
  initial?: ManualEntry;
  /** Preselected account for a new addition (from "Add offline work" on a quiet account). */
  account?: string;
  onSave: (entry: ManualEntry) => void;
  onCancel?: () => void;
}

const empty = (account: string): ManualEntry => ({
  account_key: account,
  title: "",
  what: "",
  workstream: "other",
  time: null,
  duration_min: null,
  people: [],
  follow_ups: [],
});

/** Something you did that no tool recorded. It becomes an "added by you" topic, and an event at finalize. */
export function AdditionForm({ accounts, initial, account, onSave, onCancel }: Props) {
  const first = account ?? Object.keys(accounts)[0] ?? "internal";
  const [entry, setEntry] = useState<ManualEntry>(initial ?? empty(first));
  const [people, setPeople] = useState((initial?.people ?? []).join(", "));
  const [followUps, setFollowUps] = useState((initial?.follow_ups ?? []).map((item) => item.what).join("\n"));
  const set = (patch: Partial<ManualEntry>) => setEntry({ ...entry, ...patch });
  const valid = entry.title.trim() && entry.what.trim();

  return (
    <form
      className="cw-card topic"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valid) return;
        onSave({
          ...entry,
          people: people.split(",").map((name) => name.trim()).filter(Boolean),
          follow_ups: followUps.split("\n").map((what) => what.trim()).filter(Boolean).map((what) => ({ what })),
        });
        if (!initial) {
          setEntry(empty(entry.account_key));
          setPeople("");
          setFollowUps("");
        }
      }}
    >
      <div className="topic__row">
        <span className="label">Account</span>
        <select className="field" value={entry.account_key} onChange={(event) => set({ account_key: event.target.value })}>
          {Object.entries(accounts).map(([key, name]) => (
            <option key={key} value={key}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <div className="topic__row">
        <span className="label">Title</span>
        <textarea className="field" rows={1} value={entry.title} placeholder="Call with the <CUSTOMER> data team" onChange={(event) => set({ title: event.target.value })} />
      </div>
      <div className="topic__row">
        <span className="label">What</span>
        <textarea className="field" rows={3} value={entry.what} placeholder="What happened, in your words" onChange={(event) => set({ what: event.target.value })} />
      </div>
      <div className="topic__row">
        <span className="label">When</span>
        <div className="tags">
          <input className="field" style={{ width: 110 }} type="time" value={entry.time ?? ""} onChange={(event) => set({ time: event.target.value || null })} />
          <input
            className="field"
            style={{ width: 130 }}
            type="number"
            min={0}
            placeholder="Minutes"
            value={entry.duration_min ?? ""}
            onChange={(event) => set({ duration_min: event.target.value ? Number(event.target.value) : null })}
          />
          <select className="field" style={{ width: 200 }} value={entry.workstream} onChange={(event) => set({ workstream: event.target.value as ManualEntry["workstream"] })}>
            {WORKSTREAMS.map((name) => (
              <option key={name} value={name}>
                {name.replace("-", " ")}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="topic__row">
        <span className="label">People</span>
        <textarea className="field" rows={1} value={people} placeholder="Comma-separated" onChange={(event) => setPeople(event.target.value)} />
      </div>
      <div className="topic__row">
        <span className="label">Follow-ups</span>
        <textarea className="field" rows={2} value={followUps} placeholder="One per line" onChange={(event) => setFollowUps(event.target.value)} />
      </div>
      <div className="topic__actions">
        <button className="cw-btn cw-btn--ghost cw-btn--small" type="submit" disabled={!valid}>
          {initial ? "Save addition" : "Add to the day"}
        </button>
        {onCancel && (
          <button className="icon-btn" type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
