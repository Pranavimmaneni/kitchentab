/**
 * When to offer install and push (PRD §20.1 install flows, §20.2 permission flow N1). Pure, so
 * every rule is unit-tested; `PwaRoot` feeds it the device and the per-device memory.
 *
 * Moments: `bill` (first bill sent), `ask` (first ask sent or received), `visit` (2nd visit or
 * later), `standalone` (first launch from the home screen), `settings` (Settings › Notifications).
 *
 * - The N1 pre-prompt never shows on a first load: only at `bill`, `ask`, `standalone` (iPhone
 *   push is offered again there) or from Settings (PUSH-01). Denied, two "Not now"s, a snooze or
 *   an existing subscription keep it away, except from Settings.
 * - In an iPhone browser tab push isn't possible, so N1 shows the three install steps instead; it
 *   also is the iPhone install flow at `bill` and `visit` (PWA-06).
 * - Elsewhere the install card shows at `bill` or `visit` when the browser offered
 *   `beforeinstallprompt`, at most twice per device (PWA-05). One thing at a time: push first.
 */
import { MAX_INSTALL_CARDS, MAX_NOT_NOW, type PromptMemory } from "./local";

export type Moment = "bill" | "ask" | "visit" | "standalone" | "settings";

export type DeviceState = {
  ios: boolean;
  standalone: boolean;
  pushSupported: boolean;
  permission: NotificationPermission | "unsupported";
  subscribed: boolean;
  /** `beforeinstallprompt` was captured. */
  canPrompt: boolean;
};

export type Decision =
  | { kind: "n1"; platform: "ios" | "android"; countsAsInstall: boolean }
  | { kind: "install" }
  | null;

export function pushOfferable(
  device: DeviceState,
  memory: PromptMemory,
  now: number,
  fromSettings = false,
): boolean {
  if (memory.pushDenied || device.permission === "denied") return false;
  if (device.permission === "granted" && device.subscribed) return fromSettings;
  if (fromSettings) return true;
  return memory.n1NotNow < MAX_NOT_NOW && now >= memory.n1SnoozedUntil;
}

export function decide(
  moment: Moment,
  device: DeviceState,
  memory: PromptMemory,
  now: number,
): Decision {
  const iosTab = device.ios && !device.standalone;
  const installLeft = memory.installShown < MAX_INSTALL_CARDS;
  if (moment === "settings") {
    return { kind: "n1", platform: device.ios ? "ios" : "android", countsAsInstall: false };
  }
  if (iosTab) {
    const forPush = (moment === "bill" || moment === "ask") && pushOfferable(device, memory, now);
    const forInstall = (moment === "bill" || moment === "visit") && installLeft;
    if (forPush || forInstall) return { kind: "n1", platform: "ios", countsAsInstall: forInstall };
    return null;
  }
  if (
    (moment === "bill" || moment === "ask" || moment === "standalone") &&
    device.pushSupported &&
    pushOfferable(device, memory, now)
  ) {
    return { kind: "n1", platform: device.ios ? "ios" : "android", countsAsInstall: false };
  }
  if (
    (moment === "bill" || moment === "visit") &&
    !device.standalone &&
    device.canPrompt &&
    installLeft
  ) {
    return { kind: "install" };
  }
  return null;
}

/** Writes that mark a moment (seen by the API client's `kt:write` signal). */
export function momentForWrite(method: string, path: string): Moment | null {
  if (method !== "POST") return null;
  if (/^\/bills\/[^/]+\/send$/.test(path)) return "bill";
  if (/^\/inventory\/[^/]+\/requests$/.test(path)) return "ask";
  return null;
}
