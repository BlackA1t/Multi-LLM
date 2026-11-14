import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const base = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8 };

export function WorkspaceIcon(props: IconProps) {
  return <svg {...base} {...props} aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M9 10h12"/></svg>;
}

export function RefreshIcon(props: IconProps) {
  return <svg {...base} {...props} aria-hidden="true"><path d="M20 7v5h-5"/><path d="M4.9 17A8 8 0 0 0 18.8 9M4 17v-5h5M19.1 7A8 8 0 0 0 5.2 15"/></svg>;
}

export function PlusIcon(props: IconProps) {
  return <svg {...base} {...props} aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>;
}

export function HistoryIcon(props: IconProps) {
  return <svg {...base} {...props} aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>;
}

export function ArrowIcon(props: IconProps) {
  return <svg {...base} {...props} aria-hidden="true"><path d="M5 12h14M14 7l5 5-5 5"/></svg>;
}

export function StatusIcon({ state, ...props }: IconProps & { state: "ok" | "warn" | "error" }) {
  if (state === "ok") return <svg {...base} {...props} aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>;
  if (state === "warn") return <svg {...base} {...props} aria-hidden="true"><path d="M12 3 2.8 19h18.4L12 3Z"/><path d="M12 9v4M12 16.5h.01"/></svg>;
  return <svg {...base} {...props} aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></svg>;
}
