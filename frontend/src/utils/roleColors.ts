// Canonical role -> color map (spec 05 §4). Role value strings match
// packages.schemas.Role. The API never emits labels/colors; the frontend owns them.

export const ROLE_COLORS: Record<string, string> = {
  internal: "#4F81BD",
  manager: "#9B59B6",
  account_exec: "#E67E22",
  lead: "#27AE60",
  vendor: "#E74C3C",
  unknown: "#95A5A6",
};

export const ROLE_LABELS: Record<string, string> = {
  internal: "Internal",
  manager: "Manager",
  account_exec: "Account Exec",
  lead: "Lead / Prospect",
  vendor: "Vendor / Partner",
  unknown: "Unknown",
};

export const OWNER_COLOR = "#2C3E50";

export function roleColor(role: string): string {
  return ROLE_COLORS[role] ?? ROLE_COLORS.unknown;
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}
