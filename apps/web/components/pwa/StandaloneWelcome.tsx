"use client";

import { useEffect, useState } from "react";
import { isStandalone } from "@/lib/pwa/device";
import { KEYS, getString } from "@/lib/pwa/local";
import { strings } from "@/lib/strings";

/**
 * AUTH-13 (PRD §20.1 "iPhone sign-in after install"): a Home Screen app on iOS may not share cookies
 * with the browser it was added from, so the first launch from the home screen without a session
 * says why it asks again. Google, email and phone codes are right below on the sign-in screen.
 */
export function StandaloneWelcome() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    setShow(isStandalone() && !getString(KEYS.standaloneSignedIn));
  }, []);
  if (!show) return null;
  return (
    <div className="kt-banner kt-welcome-back" role="status">
      <p className="kt-banner__text">{strings.pwa.welcomeBack}</p>
      <p className="kt-welcome-back__sub">{strings.pwa.welcomeBackSub}</p>
    </div>
  );
}
