import { useEffect } from "react";
import { Landing } from "./components/Landing";
import { redirect, usePathname } from "./router";
import { Workspace } from "./workspace/Workspace";

/**
 * Top-level route switch (S12).
 *
 * - /              → marketing landing page (S12.4).
 * - /app, /app/*   → the workspace shell.
 * - anything else  → redirected to the landing page.
 */
export default function App() {
  const pathname = usePathname();
  const inApp = pathname === "/app" || pathname.startsWith("/app/");
  const isLanding = pathname === "/" || pathname === "";

  useEffect(() => {
    if (!inApp && !isLanding) redirect("/");
  }, [inApp, isLanding]);

  if (inApp) return <Workspace />;
  if (isLanding) return <Landing />;
  return null; // redirecting to "/"
}
