import { useEffect, useState } from "react";
import type { FollowUp } from "../types";
import { Inline } from "./Inline";
import { ReviewLabel } from "./ReviewLabel";

interface Props {
  topicId: string;
  followUp: FollowUp;
  editable: boolean;
  onEdit: (patch: Partial<FollowUp>) => void;
  onRemove: () => void;
  onDropFollowUp: (fromTopicId: string, followUpId: string) => void;
}

export const FOLLOW_UP_MIME = "application/x-fde-follow-up";

/** One follow-up: its text, then owner and due on one line; "reviewed" (your progress mark) and Remove on the
 *  right. Drag it onto another follow-up or topic to move it there. */
export function FollowUpRow({ topicId, followUp, editable, onEdit, onRemove, onDropFollowUp }: Props) {
  const [over, setOver] = useState(false);
  const [reviewed, setReviewed] = useState(followUp.reviewed);
  useEffect(() => setReviewed(followUp.reviewed), [followUp.reviewed]);

  return (
    <div
      className={`followup${over ? " is-drop" : ""}${reviewed ? " is-reviewed" : ""}`}
      draggable={editable}
      onDragStart={(event) => {
        event.dataTransfer.setData(FOLLOW_UP_MIME, JSON.stringify({ topicId, followUpId: followUp.id }));
        event.stopPropagation();
      }}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes(FOLLOW_UP_MIME)) {
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
          event.stopPropagation();
          const { topicId: from, followUpId } = JSON.parse(data);
          if (from !== topicId) onDropFollowUp(from, followUpId);
        }
      }}
    >
      <div className="followup__body">
        <Inline multiline value={followUp.what} disabled={!editable} onSave={(what) => what && onEdit({ what })} ariaLabel="Follow-up" />
        <div className="followup__meta">
          <label className="followup__field">
            <span className="tag">Owner</span>
            <Inline
              multiline
              className="inline--small"
              value={followUp.owner ?? ""}
              placeholder="you"
              disabled={!editable}
              onSave={(owner) => onEdit({ owner: owner || null })}
              ariaLabel="Owner"
            />
          </label>
          <label className="followup__field">
            <span className="tag">Due</span>
            <Inline
              className="inline--small"
              value={followUp.due_if_stated ?? ""}
              placeholder="next working day"
              disabled={!editable}
              onSave={(due) => onEdit({ due_if_stated: due || null })}
              ariaLabel="Due"
            />
          </label>
          {followUp.todoist && (
            <a
              className="followup__task"
              href={followUp.todoist.url}
              target="_blank"
              rel="noreferrer"
              title={
                followUp.todoist.action === "commented"
                  ? `Added as a comment to the open task "${followUp.todoist.title ?? ""}"`
                  : `Open in Todoist${followUp.todoist.target ? `: ${followUp.todoist.target}` : ""}${
                      followUp.todoist.due_date ? `, due ${followUp.todoist.due_date}` : ""
                    }`
              }
            >
              <span className="followup__task-dot" aria-hidden="true" />
              {followUp.todoist.action === "commented" ? "Commented in Todoist" : "In Todoist"}
              {followUp.todoist.target ? ` · ${followUp.todoist.target.split(" / ").pop()}` : ""} ↗
            </a>
          )}
        </div>
      </div>
      {editable && (
        <div className="followup__actions">
          <button
            className={`review-btn review-btn--small${reviewed ? " is-on" : ""}`}
            aria-pressed={reviewed}
            title="Your progress mark; it does not change what is sent to Todoist"
            onClick={() => {
              setReviewed(!reviewed);
              onEdit({ reviewed: !reviewed });
            }}
          >
            <ReviewLabel reviewed={reviewed} />
          </button>
          <button className="icon-btn" onClick={onRemove} aria-label="Remove follow-up">
            Remove
          </button>
        </div>
      )}
    </div>
  );
}
