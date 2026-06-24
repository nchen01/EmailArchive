import { useSyncExternalStore } from "react";

/**
 * Minimal dependency-free client router (S12).
 *
 * The app has a small, fixed set of routes (landing + a handful of workspace
 * screens), so a full router library would be overkill. This uses the History
 * API plus useSyncExternalStore to track `location.pathname`. Vite's dev server
 * and `vite preview` both serve index.html for unknown paths (SPA fallback), so
 * deep links like /app/network load correctly.
 */

function subscribe(callback: () => void): () => void {
  window.addEventListener("popstate", callback);
  return () => window.removeEventListener("popstate", callback);
}

function getSnapshot(): string {
  return window.location.pathname;
}

/** Reactive current pathname. */
export function usePathname(): string {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** Imperative navigation. Pushes history and notifies subscribers. */
export function navigate(to: string): void {
  if (to === window.location.pathname) return;
  window.history.pushState({}, "", to);
  // pushState does not emit popstate; dispatch one so usePathname re-renders.
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** Replace the current entry (used for redirects so Back does not loop). */
export function redirect(to: string): void {
  if (to === window.location.pathname) return;
  window.history.replaceState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

interface LinkProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  to: string;
}

/** Anchor that navigates client-side, while remaining a real, copyable link. */
export function Link({ to, onClick, children, ...rest }: LinkProps) {
  return (
    <a
      href={to}
      onClick={(e) => {
        // Respect modifier-clicks (open in new tab) and non-left clicks.
        if (
          e.defaultPrevented ||
          e.button !== 0 ||
          e.metaKey ||
          e.ctrlKey ||
          e.shiftKey ||
          e.altKey
        ) {
          return;
        }
        e.preventDefault();
        navigate(to);
        onClick?.(e);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}
