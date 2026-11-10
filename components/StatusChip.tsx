// Status as text + colour, never colour alone (UI_UX §3, §6).

type Tone = "success" | "accent" | "muted" | "warning" | "danger";

const TONES: Record<Tone, string> = {
  success: "bg-success/10 text-success",
  accent: "bg-accent/10 text-accent",
  muted: "bg-line text-muted",
  warning: "bg-warning/10 text-warning",
  danger: "bg-danger/10 text-danger",
};

export function StatusChip({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}
