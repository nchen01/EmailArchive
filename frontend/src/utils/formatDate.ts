/**
 * Locale-aware short date ("Mar 5, 2026"). Returns "—" for null/empty and
 * echoes an unparseable string back unchanged. Shared by the evidence and
 * relationship detail views so date rendering stays consistent.
 */
export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
