"use client";

import { useState } from "react";
import { strings } from "@/lib/strings";

export type ActivityKind = "edit" | "payment" | "system";

export type ActivityEvent = {
  id: string;
  kind: ActivityKind;
  text: string;
  /** Preformatted timestamp, e.g. "SUN 10/04 · 9:12 PM". */
  when: string;
  /** Machine-readable time for <time dateTime>. */
  dateTime?: string;
};

export type ActivityTimelineProps = {
  /** Newest first. */
  events: readonly ActivityEvent[];
  /** Show this many, then "Show more" (PRD §14A: 10). */
  limit?: number;
};

/** Dotted spine with 12 px square markers (orange edits, lime payments, paper other). */
export function ActivityTimeline({ events, limit = 10 }: ActivityTimelineProps) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? events : events.slice(0, limit);
  return (
    <div className="kt-timeline">
      <ol className="kt-timeline__list">
        {shown.map((event) => (
          <li key={event.id} className="kt-timeline__event" data-kind={event.kind}>
            <span className="kt-timeline__marker" aria-hidden="true" />
            <div className="kt-timeline__content">
              <span className="sr-only">{strings.activity.kinds[event.kind]}: </span>
              <span className="kt-timeline__text">{event.text}</span>
              <time className="kt-timeline__when" dateTime={event.dateTime}>
                {event.when}
              </time>
            </div>
          </li>
        ))}
      </ol>
      {!expanded && events.length > limit ? (
        <button type="button" className="kt-text-button" onClick={() => setExpanded(true)}>
          {strings.activity.showMore}
        </button>
      ) : null}
    </div>
  );
}
