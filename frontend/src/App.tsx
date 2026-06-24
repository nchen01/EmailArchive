import { useEffect } from "react";
import { redirect, usePathname } from "./router";
import { Workspace } from "./workspace/Workspace";

/**
 * Top-level route switch (S12).
 *
 * - /app and /app/*  → the workspace shell.
 * - everything else  → redirected to /app for now. The marketing landing page
 *   at "/" is built in S12.4; until then "/" lands directly in the workspace.
 */
export default function App() {
  const pathname = usePathname();
  const inApp = pathname === "/app" || pathname.startsWith("/app/");

  useEffect(() => {
    if (!inApp) redirect("/app");
  }, [inApp]);

  if (!inApp) return null; // redirecting
  return <Workspace />;
}
