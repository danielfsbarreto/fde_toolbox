import type { Day, Review } from "./types";

export type Tone = "action" | "busy" | "done" | "idle";

/** Where a day stands, said the same way everywhere (breadcrumb bar, bottom bar, list of days). */
export function dayState(status: Day["status"], review: Review | null): { label: string; tone: Tone; next: string } {
  if (status === "working") {
    return { label: "The agent is working", tone: "busy", next: "The day comes back here when it is done." };
  }
  if (review?.status === "waiting" && review.can_answer) {
    return {
      label: `Round ${review.round} · waiting for your review`,
      tone: "action",
      next: "Review each topic, then ask the agent for more or finalize the day.",
    };
  }
  if (review?.status === "waiting") {
    return {
      label: "Paused · answer in AMP",
      tone: "idle",
      next: "AMP's notification did not reach the app: answer this round in AMP. Your edits are saved.",
    };
  }
  if (status === "final") {
    return {
      label: "Final",
      tone: "done",
      next: "The record of this day: read-only. To redo it, delete it in Settings and run it again.",
    };
  }
  return { label: "Draft", tone: "idle", next: "The flow is not waiting for a review of this day. Your edits are saved." };
}

export function dayName(date: string): { weekday: string; rest: string; short: string } {
  const day = new Date(`${date}T12:00:00`);
  const weekday = day.toLocaleDateString("en-US", { weekday: "long" });
  const month = day.toLocaleDateString("en-US", { month: "long" });
  const short = day.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  return { weekday, rest: `${month} ${day.getDate()}`, short };
}
