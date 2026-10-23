import { useEffect, useState } from "react";
import { api } from "../../api";
import type { EnvStatus, EnvVar } from "../../types";
import { Inline } from "./Markdown";

// Every checklist on the page (the gate, the Values panel, the Design tab) reads again when one of them saves
const CHANGED = "build-env-changed";

interface Props {
  buildId: string;
  vars: EnvVar[];
  onChange?: () => void;
  /** Only which are set: the values are set in the design's gate (the conversation) or the build's Environment variables panel */
  readOnly?: boolean;
}

/** The values the project needs to run in its sandbox: which are set, and a place to set them. Values are sent to the
 *  server once, stored encrypted, and never shown again (never through the chat). */
export function EnvChecklist({ buildId, vars, onChange, readOnly = false }: Props) {
  const [status, setStatus] = useState<EnvStatus | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const read = () =>
      api
        .buildEnv(buildId)
        .then((data) => live && setStatus(data))
        .catch(() => live && setError("Could not read which environment variables are set."));
    read();
    window.addEventListener(CHANGED, read);
    return () => {
      live = false;
      window.removeEventListener(CHANGED, read);
    };
  }, [buildId]);

  const isSet = new Set(status?.set.map((item) => item.name) ?? []);
  const required = vars.filter((v) => v.required !== false); // what the counts are about; optional ones don't block

  async function act(request: Promise<EnvStatus>) {
    setBusy(true);
    setError(null);
    try {
      setStatus(await request);
      setEditing(null);
      setValue("");
      window.dispatchEvent(new Event(CHANGED));
      onChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="env">
      <p className="design__when">
        {status ? `${required.filter((v) => isSet.has(v.name)).length} of ${required.length} set` : "Checking…"}
      </p>
      {status && !status.can_store && !readOnly && (
        <p className="env__note">Environment variables can't be stored yet: BUILD_ENV_KEY is not set on the review app.</p>
      )}
      <ul className="design__needs">
        {vars.map((v) => {
          const set = isSet.has(v.name);
          const others = status?.elsewhere[v.name] ?? [];
          return (
            <li key={v.name} className="env__row">
              <span className={`design__conn ${set ? "is-connected" : v.required === false ? "" : "is-not_connected"}`}>
                {set ? "✓" : "○"}
              </span>
              <div className="env__what">
                <code>{v.name}</code> {v.required === false && <span className="tag">optional</span>}
                {v.purpose && (
                  <div className="design__when">
                    <Inline>{v.purpose}</Inline>
                  </div>
                )}
              </div>
              {readOnly ? null : editing === v.name ? (
                <form
                  className="env__form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (value.trim()) act(api.setBuildEnv(buildId, v.name, value));
                  }}
                >
                  <input
                    className="field env__input"
                    type={v.secret ? "password" : "text"}
                    autoComplete="off"
                    autoFocus
                    placeholder={`${v.name} value`}
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    onKeyDown={(event) => event.key === "Escape" && setEditing(null)}
                  />
                  <button className="icon-btn" type="submit" disabled={busy || !value.trim()}>
                    Save
                  </button>
                  <button className="icon-btn" type="button" onClick={() => setEditing(null)}>
                    Cancel
                  </button>
                </form>
              ) : (
                status?.can_store && (
                  <span className="env__actions">
                    <button
                      className="icon-btn"
                      onClick={() => {
                        setEditing(v.name);
                        setValue("");
                      }}
                    >
                      {set ? "Replace" : "Set"}
                    </button>
                    {!set &&
                      others.slice(0, 2).map((other) => (
                        <button
                          key={other.build_id}
                          className="icon-btn"
                          disabled={busy}
                          title={`Copy the value you set in "${other.title}"`}
                          onClick={() => act(api.reuseBuildEnv(buildId, v.name, other.build_id))}
                        >
                          Use from {other.title}
                        </button>
                      ))}
                    {set && (
                      <button className="icon-btn" disabled={busy} onClick={() => act(api.deleteBuildEnv(buildId, v.name))}>
                        Remove
                      </button>
                    )}
                  </span>
                )
              )}
            </li>
          );
        })}
      </ul>
      {error && <p className="env__note">{error}</p>}
    </div>
  );
}
