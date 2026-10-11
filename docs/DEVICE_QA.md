# Device QA on real phones (TEST_PLAN §5)

Run this after `docs/LAUNCH.md` steps 1–8, against the production URL printed by `make deploy`
(below: `https://kitchentab.vercel.app`; use yours). Record device, OS version, browser and the
result of every line in `docs/TEST_REPORT.md` under "Device QA".

## Test accounts

Real sign-in codes go to Gmail after `make set-email`. Gmail ignores everything after a `+`, so
one inbox gives you as many accounts as you need:

| Role | Email | Device |
|---|---|---|
| Payer (you) | `you@gmail.com` | iPhone |
| Roommate A | `you+qa-a@gmail.com` | Android phone |
| Roommate B | `you+qa-b@gmail.com` | Laptop (Chrome or Edge) |
| Roommate C | `you+qa-c@gmail.com` | Laptop, second browser (Firefox or Safari) |

Create one house "QA House" as the payer with roommates A, B and C, and claim each name from its
device with the invite link. Delete the QA house afterwards (House → Leave/Remove), or keep it
separate from your real house.

Time needed: about 60 minutes for everything; the starred lines (★) are the 15-minute minimum.

## 1. iPhone, Safari (latest iOS, and one version back if you can borrow one)

- [ ] ★ Open the invite link in Safari → the preview card shows in iMessage/WhatsApp → claim your name.
- [ ] ★ Share → **Add to Home Screen** → open KitchenTab from the Home Screen icon.
- [ ] Note whether a second sign-in was needed in the Home Screen app (expected: yes, once:
      "Welcome back. Sign in once more on this app"). Record the result; it feeds ADR "iPhone sign-in".
- [ ] ★ Tap **Turn on notifications** (N1) → allow.
- [ ] From the Android phone or a laptop, trigger every event type; each notification arrives
      and tapping opens the right screen:
  - [ ] Ask received (roommate asks for an item you own) → Requests
  - [ ] Ask answered (you asked; they say yes) → item sheet
  - [ ] New tab includes you (someone else sends a bill with you on it) → the bill
  - [ ] Your share changed by ≥ $0.50 (they tick themselves onto a shared item; arrives ~10 min later)
  - [ ] Payment recorded (on a bill you paid) → the bill · Payment confirmed / rejected → the bill
  - [ ] Reminder to pay (they remind you)
  - [ ] Use soon round-up (9:00 house time the next morning, if anything is near its date)
  - [ ] Someone joined the house (you are admin)
- [ ] ★ App icon badge = pending asks + unseen share changes; it clears after you open them.
- [ ] Settings › Notifications › **Private on the lock screen** on → a notification shows no names,
      items or amounts → off again.
- [ ] Quiet hours: set them to start in 2 minutes and end in 7 → trigger two asks inside the window
      → nothing arrives until the end, then **one** grouped notification ("2 asks for your food").
- [ ] Deny notifications (Settings app → KitchenTab → Notifications off) → Settings › Notifications
      shows "Blocked"; delete the Home Screen app, re-add it, and you're asked again.
- [ ] ★ Dark mode (Control Center) on every screen you visit.

## 2. iPhone, Chrome and Edge

- [ ] Add to Home Screen from Chrome's share menu works; push works from the installed app.
- [ ] Same in Edge.

## 3. Android (Pixel or Samsung), Chrome

- [ ] ★ The install card appears after the first bill is sent (or on the second visit) → **Install**.
- [ ] Push arrives both in a normal Chrome tab and in the installed app.
- [ ] Long-press the icon: shortcuts **Snap a receipt**, **Fridge**, **Asks** open the right screens.
- [ ] Badge (dot or count, depending on launcher) appears for pending asks.
- [ ] ★ Dark mode on every screen you visit.

## 4. Desktop

- [ ] Chrome or Edge (Windows or macOS): install from the address-bar icon or Settings → Install app;
      push arrives; tapping a notification focuses the open window and navigates.
- [ ] Firefox: push in a tab (no install).
- [ ] Safari on macOS: File → Add to Dock; push from the Dock app.
- [ ] Open the same bill in three tabs: ticks in one tab show in the others within 2 s; close the
      first tab and the others keep updating (leader handover).

## 5. Airplane mode

- [ ] ★ iPhone: open a bill, switch on Airplane mode, tick an item → write controls are disabled with
      "Needs a connection", the banner says "YOU'RE OFFLINE · SHOWING SAVED TABS"; nothing is queued.
      Switch it off → the screen catches up; no duplicate tick.
- [ ] Android: start recording a payment, switch on Airplane mode before tapping Save → a clear
      error with Retry; after reconnecting, Retry records it **once** (check the bill's payments).

## 6. Two phones side by side

- [ ] ★ Both phones on the same open bill; both tick and untick items quickly for 2 minutes. The two
      screens always end up identical; totals never flicker between wrong values; Σ shares equals
      the receipt total on both.

## 7. Screen readers

- [ ] VoiceOver (iPhone): sign in, create a house, add a bill by hand, split, send (E2E-01); claim a
      name from a link and tick your items (E2E-02); ask for an item and answer it (E2E-04). Every
      control is announced; split cells read "{Name} uses {Item}".
- [ ] TalkBack (Android): the same three journeys.

## 8. Dark mode everywhere

- [ ] Each screen on each device in light and dark; nothing unreadable, no white flashes on load.

## 9. Sign-in email authentication (EMAIL-04)

- [ ] ★ In Gmail, open a KitchenTab sign-in code email → ⋮ → **Show original**: SPF, DKIM and DMARC
      all say **PASS**. Codes go out through the KitchenTab Gmail account, so the From domain is
      gmail.com and Google publishes those records; if KitchenTab ever sends from its own domain,
      that domain needs SPF, DKIM and DMARC set up first.

## What to look at first if something is off

1. **Push on iPhone**: only works from the Home Screen app on iOS 16.4+, and only after a tap on
   "Turn on notifications". If nothing arrives, check Settings › Notifications in KitchenTab (devices
   list) and the Vercel dashboard → Queues (messages flowing?) and Logs (filter `api`).
2. **Sign-in on the installed iPhone app**: the Home Screen app keeps its own cookies.
3. **Sync between phones**: if one phone lags, pull to refresh; if it stays behind, note the time and
   check Logs for `/events` errors (streams reconnect every 270 s by design).
