/**
 * Theme (light/dark) for the app (S17 visual system).
 *
 * The palette lives in CSS custom properties (see index.css); this module only
 * decides which theme is active and stamps `data-theme` on <html>, which the
 * token overrides key off. Preference order: an explicit saved choice, else the
 * OS `prefers-color-scheme`. Applied before React renders (from main.tsx) to
 * avoid a flash of the wrong theme.
 */
import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
const KEY = "ekc_theme";
const listeners = new Set<() => void>();

function systemPref(): Theme {
  return typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function saved(): Theme | null {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** The theme that should currently apply. */
export function currentTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

/** Stamp a theme onto <html>. Called at startup and on toggle. */
export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

/** Resolve + apply the initial theme (saved choice, else OS preference). */
export function initTheme(): void {
  applyTheme(saved() ?? systemPref());
}

/** Flip and persist the choice. */
export function toggleTheme(): void {
  const next: Theme = currentTheme() === "dark" ? "light" : "dark";
  try {
    window.localStorage.setItem(KEY, next);
  } catch {
    /* storage unavailable — the choice just won't persist */
  }
  applyTheme(next);
  for (const l of listeners) l();
}

/** Reactive current theme for components (e.g. the toggle icon). */
export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => currentTheme(),
    () => "light",
  );
}
