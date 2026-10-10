import type { Metadata } from "next";
import { NotifySettingsScreen } from "./NotifySettingsScreen";

export const metadata: Metadata = { title: "Notifications" };

/** N2 · Settings › Notifications (PRD §20.2, NotifySettings.dc.html). */
export default function NotificationSettingsPage() {
  return <NotifySettingsScreen />;
}
