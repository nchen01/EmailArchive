import { useEffect } from "react";
import { Landing } from "./components/Landing";
import { RecipientPackage } from "./components/RecipientPackage";
import { redirect, usePathname } from "./router";
import { Workspace } from "./workspace/Workspace";

/**
 * Top-level route switch (S12).
 *
 * - /                     → marketing landing page (S12.4).
 * - /app, /app/*          → the workspace shell (creator/operator).
 * - /handoff/recipient    → the delivered read-only recipient package (S17.6).
 *                           Opened via a share link with a #c=<code> fragment.
 * - anything else         → redirected to the landing page.
 */
export default function App() {
  const pathname = usePathname();
  const inApp = pathname === "/app" || pathname.startsWith("/app/");
  const isLanding = pathname === "/" || pathname === "";
  const isRecipient = pathname === "/handoff/recipient";

  useEffect(() => {
    if (!inApp && !isLanding && !isRecipient) redirect("/");
  }, [inApp, isLanding, isRecipient]);

  if (inApp) return <Workspace />;
  if (isRecipient) return <RecipientPackage />;
  if (isLanding) return <Landing />;
  return null; // redirecting to "/"
}
