import { useEffect, useRef, useState } from "react";
import type { FollowUp, Topic } from "../types";
import { WORKSTREAMS } from "../types";
import { FOLLOW_UP_MIME, FollowUpRow } from "./FollowUpRow";
import { Inline } from "./Inline";
import { ReviewLabel } from "./ReviewLabel";

export const TOPIC_MIME = "application/x-fde-topic";

interface Props {
  topic: Topic;
  /** Collapsed, a topic is one compact row; expanded, every field shows and can be edited. */
  expanded: boolean;
  onToggle: () => void;
  accounts: Record<string, string>;
  editable: boolean;
  onEdit: (patch: Partial<Topic>) => void | Promise<void>;
  onMove: (accountKey: string) => void;
  onRemove: () => void;
  onAsk: () => void;
  onFollowUpEdit: (followUpId: string, patch: Partial<FollowUp>) => void;
  onFollowUpAdd: (what: string) => void;
  onFollowUpRemove: (followUpId: string) => void;
  onFollowUpRestore: (followUpId: string) => void;
  onFollowUpMove: (fromTopicId: string, followUpId: string) => void;
}

function linkLabel(url: string): string {
  const host = new URL(url).hostname;
  const known: [string, string][] = [
    ["slack.com", "Slack"], ["linear.app", "Linear"], ["github.com", "GitHub"], ["mail.google.com", "Gmail"],
    ["google.com", "Calendar"], ["teams.microsoft.com", "Teams"], ["fathom.video", "Fathom"], ["granola.ai", "Granola"],
  ];
  return known.find(([domain]) => host.endsWith(domain))?.[1] ?? "Link";
}

const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);

export function TopicCard(props: Props) {
  const { topic, accounts, onEdit } = props;
  const manual = topic.attribution === "manual";
  const editable = props.editable && !manual;
  const [dragging, setDragging] = useState(false);
  const [over, setOver] = useState(false);
  const [newFollowUp, setNewFollowUp] = useState("");
  const [reviewed, setReviewed] = useState(topic.reviewed);
  useEffect(() => setReviewed(topic.reviewed), [topic.reviewed]);
  // Marking a topic reviewed marks all its follow-ups on the server: the card stays locked (inert) until that is saved
  const [saving, setSaving] = useState(false);
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    card.current?.toggleAttribute("inert", saving);
  }, [saving]);
  const toggleReviewed = async () => {
    setReviewed(!reviewed);
    setSaving(true);
    try {
      await onEdit({ reviewed: !reviewed });
    } finally {
      setSaving(false);
    }
  };

  return (
    <article
      ref={card}
      id={`topic-${topic.id}`}
      data-topic={topic.id}
      className={`cw-card topic${props.expanded ? "" : " is-collapsed"}${reviewed ? " is-reviewed" : ""}${saving ? " is-saving" : ""}${dragging ? " is-dragging" : ""}${over ? " is-drop" : ""}`}
      aria-busy={saving}
      draggable={editable && !saving}
      onDragStart={(event) => {
        event.dataTransfer.setData(TOPIC_MIME, topic.id);
        setDragging(true);
      }}
      onDragEnd={() => setDragging(false)}
      onDragOver={(event) => {
        if (editable && event.dataTransfer.types.includes(FOLLOW_UP_MIME)) {
          event.preventDefault();
          setOver(true);
        }
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        setOver(false);
        const data = event.dataTransfer.getData(FOLLOW_UP_MIME);
        if (data) {
          event.preventDefault();
          const { topicId, followUpId } = JSON.parse(data);
          if (topicId !== topic.id) props.onFollowUpMove(topicId, followUpId);
        }
      }}
    >
      <div className="topic__head">
        <div className="tags">
          {topic.agent_added && <span className="tag tag--new">New</span>}
          {manual ? (
            <span className="tag">Added by you</span>
          ) : (
            <select
              className="icon-btn tag"
              value={topic.workstream}
              disabled={!editable}
              aria-label="Workstream"
              onChange={(event) => onEdit({ workstream: event.target.value as Topic["workstream"] })}
            >
              {WORKSTREAMS.map((name) => (
                <option key={name} value={name}>
                  {name.replace("-", " ")}
                </option>
              ))}
            </select>
          )}
          <span className="tag">{topic.my_involvement}</span>
          {topic.attribution === "content" && <span className="tag">Account inferred</span>}
          {topic.attribution === "review" && !topic.agent_added && <span className="tag">Changed in review</span>}
          {topic.inferred && <span className="tag">Contains inferences</span>}
        </div>
        {/* The reviewed toggle: top right, the same place whether the card is open or not */}
        {!manual && props.editable ? (
          <button
            className={`review-btn${reviewed ? " is-on" : ""}`}
            aria-pressed={reviewed}
            title={reviewed ? "Click to mark it not reviewed" : "Mark it reviewed (its follow-ups too)"}
            onClick={toggleReviewed}
          >
            <ReviewLabel reviewed={reviewed} saving={saving} />
          </button>
        ) : (
          reviewed && <span className="chip chip--on">✓ Reviewed</span>
        )}
      </div>

      {props.expanded ? (
        <div className="topic__title">
          <Inline multiline className="inline--serif" value={topic.title} disabled={!editable} onSave={(title) => title && onEdit({ title })} ariaLabel="Title" />
        </div>
      ) : (
        <button className="topic__title topic__title--toggle" onClick={props.onToggle} aria-expanded={false}>
          {topic.title}
        </button>
      )}

      {!props.expanded && (
        <>
          <p className="topic__summary">{topic.what_happened}</p>
          <div className="topic__meta">
            <span className="tag">
              {topic.follow_ups.length} follow-up{topic.follow_ups.length === 1 ? "" : "s"}
              {topic.removed_follow_ups?.length ? ` · ${topic.removed_follow_ups.length} removed` : ""}
            </span>
            <button className="icon-btn" onClick={props.onToggle} aria-expanded={false}>
              Details ▾
            </button>
          </div>
        </>
      )}

      {props.expanded && (
      <>
      <Inline
        multiline
        value={topic.what_happened}
        disabled={!editable}
        onSave={(what_happened) => onEdit({ what_happened })}
        ariaLabel="What happened"
      />

      <div className="topic__row">
        <span className="label">Status</span>
        <Inline
          multiline
          value={topic.status_or_decision ?? ""}
          placeholder="No status"
          disabled={!editable}
          onSave={(status) => onEdit({ status_or_decision: status || null })}
        />
      </div>
      <div className="topic__row">
        <span className="label">Risks</span>
        <Inline
          multiline
          value={topic.risks.join("\n")}
          placeholder="No risks (one per line)"
          disabled={!editable}
          onSave={(text) => onEdit({ risks: lines(text) })}
        />
      </div>
      <div className="topic__row">
        <span className="label">People</span>
        <Inline
          multiline
          value={topic.people.join(", ")}
          placeholder="Nobody named"
          disabled={!editable}
          onSave={(text) => onEdit({ people: text.split(",").map((name) => name.trim()).filter(Boolean) })}
        />
      </div>

      <div className="topic__row">
        <span className="label">Follow-ups</span>
        <div className="followups">
          {topic.follow_ups.map((followUp) => (
            <FollowUpRow
              key={followUp.id}
              topicId={topic.id}
              followUp={followUp}
              editable={editable}
              onEdit={(patch) => props.onFollowUpEdit(followUp.id, patch)}
              onRemove={() => props.onFollowUpRemove(followUp.id)}
              onDropFollowUp={props.onFollowUpMove}
            />
          ))}
          {props.editable && (topic.removed_follow_ups ?? []).map((item) => (
            <div className="followup followup--removed" key={item.id}>
              <div className="followup__body">
                <span className="followup__what">{item.what}</span>
                <span className="tag">Removed</span>
              </div>
              {editable && (
                <div className="followup__actions">
                  <button className="icon-btn" onClick={() => props.onFollowUpRestore(item.id)}>
                    Restore
                  </button>
                </div>
              )}
            </div>
          ))}
          {editable && (
            <input
              className="field"
              placeholder="Add a follow-up and press Enter"
              value={newFollowUp}
              onChange={(event) => setNewFollowUp(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && newFollowUp.trim()) {
                  props.onFollowUpAdd(newFollowUp.trim());
                  setNewFollowUp("");
                }
              }}
            />
          )}
        </div>
      </div>

      {topic.links.length > 0 && (
        <div className="topic__row">
          <span className="label">Evidence</span>
          <div className="topic__links">
            {topic.links.slice(0, 8).map((url) => (
              <a key={url} href={url} target="_blank" rel="noreferrer">
                {linkLabel(url)}
              </a>
            ))}
          </div>
        </div>
      )}

      </>
      )}

      {props.expanded && (
          <div className="topic__actions">
            <button className="icon-btn" onClick={props.onToggle} aria-expanded>
              Hide details ▴
            </button>
            {!manual && props.editable && (
            <>
            <select
              className="icon-btn"
              value=""
              aria-label="Move to another account"
              onChange={(event) => event.target.value && props.onMove(event.target.value)}
            >
              <option value="">Move to…</option>
              {Object.entries(accounts)
                .filter(([key]) => key !== topic.account_key)
                .map(([key, name]) => (
                  <option key={key} value={key}>
                    {name}
                  </option>
                ))}
            </select>
            <button className="icon-btn" onClick={props.onAsk}>
              Ask the agent…
            </button>
            <button className="icon-btn" onClick={props.onRemove}>
              Remove
            </button>
            </>
            )}
          </div>
        )}
    </article>
  );
}
