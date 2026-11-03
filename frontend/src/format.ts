const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-11-06" -> "Nov 6". Parsed by hand so the day never shifts with the viewer's timezone. */
export function shortDate(iso: string | null): string {
  if (!iso) return "No deadline";
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

export function longDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const dayNumber = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
};

/** How far a deadline is from `today`, in words a person would say. */
export function deadlineWords(deadline: string | null, today: string): { text: string; tone: "overdue" | "soon" | "calm" | "none" } {
  if (!deadline) return { text: "No deadline", tone: "none" };
  const diff = dayNumber(deadline) - dayNumber(today);
  if (diff < 0) return { text: `${-diff} day${diff === -1 ? "" : "s"} overdue`, tone: "overdue" };
  if (diff === 0) return { text: "Due today", tone: "soon" };
  if (diff <= 7) return { text: `Due in ${diff} day${diff === 1 ? "" : "s"}`, tone: "soon" };
  return { text: `Due ${shortDate(deadline)}`, tone: "calm" };
}

export function clock(ms: number | null): string | null {
  if (ms == null) return null;
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const initials = (name: string | null) => (name ? name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase() : "?");

export const sourceLabel = (kind: string) =>
  ({ audio: "Meeting recording", transcript: "Meeting transcript", email: "Email", text: "Text update" } as Record<string, string>)[kind] ?? kind;
