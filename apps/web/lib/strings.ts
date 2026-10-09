/**
 * The one string catalog for UI copy (PRD §13 Localization: English only, all strings in one
 * file). Copy in quotes in PRD §14A is final; keep it word for word.
 */
export const strings = {
  app: {
    name: "KitchenTab",
    description: "Split grocery receipts item by item and share what's in the kitchen.",
    brandLine: (house: string) => `KITCHENTAB ** ${house.toUpperCase()}`,
  },
  theme: {
    toDark: "Switch to dark mode",
    toLight: "Switch to light mode",
    system: "System",
    light: "Light",
    dark: "Dark",
    appearance: "Appearance",
  },
  header: {
    back: "Back",
    more: "More actions",
    snapReceipt: "Snap a receipt",
    snapShort: "Snap",
  },
  nav: {
    label: "Main",
    kitchen: "KITCHEN",
    tabs: "TABS",
    balances: "BALANCES",
    house: "HOUSE",
  },
  kitchen: {
    title: "The kitchen",
    emptyTitle: "Empty. Snap a receipt to stock it.",
    comingSoon: "Your fridge and pantry will show up here.",
    fridge: "Fridge",
    asks: "Asks",
  },
  button: {
    loading: "Working on it",
    needsConnection: "Needs a connection",
  },
  split: {
    title: "Who's eating what?",
    gridLabel: "Who's eating what",
    item: "ITEM",
    nobodyYet: "NOBODY YET",
    checkboxLabel: (member: string, item: string) => `${member} uses ${item}`,
    splitRest: "Split the rest with everyone",
    itemsLeft: (n: number) => `${n} ${n === 1 ? "ITEM" : "ITEMS"} LOOKING FOR A HOME`,
    allSorted: "All sorted → review",
    totalsLabel: "Totals so far",
    taxNote: (tax: string) => `TAX ${tax} · SPLIT BY EACH PERSON'S SHARE`,
    conflict: "Someone else changed this tab. We refreshed it.",
    progressLabel: "Items assigned",
    progressText: (done: number, total: number) => `${done} of ${total} items assigned`,
  },
  otp: {
    label: "6-digit code",
    error: "That code doesn't match. Check it and try again.",
    autofillHint: "Codes auto-fill from email and SMS on most phones.",
  },
  review: {
    check: "CHECK?",
    edit: (item: string) => `Edit ${item}`,
    storage: { fridge: "FRIDGE", freezer: "FREEZER", pantry: "PANTRY", none: "NOT FOOD" },
    storageLabel: (item: string, place: string) =>
      `${item} goes in: ${place.toLowerCase()}. Tap to change.`,
    nameField: "Item name",
    priceField: "Price",
    save: "Save",
    cancel: "Cancel",
    tagsHelp: "TAGS SAY WHERE IT GOES · TAP ONE TO CHANGE IT",
  },
  people: {
    itemCount: (n: number) => `${n} ${n === 1 ? "ITEM" : "ITEMS"}`,
    paidTheStore: "PAID THE STORE",
    guest: "GUEST",
    you: "You",
  },
  owes: {
    heading: "WHO OWES WHAT",
    notPaid: "NOT PAID YET",
    pending: (amount: string) => `PAID ${amount} · PENDING`,
    paid: (amount: string) => `PAID ${amount}`,
    credit: (amount: string) => `OVERPAID ${amount}`,
    paidTheStore: "PAID THE STORE",
    nudge: (name: string) => `Nudge ${name}`,
    confirm: "Confirm",
    reject: "Reject",
    pay: "Pay",
    stillOpen: (day: number) => `STILL OPEN · DAY ${day}`,
  },
  activity: {
    heading: "THE STORY SO FAR",
    showMore: "Show more",
    kinds: { edit: "Edit", payment: "Payment", system: "Update" },
  },
  balance: {
    heading: "BALANCE",
    netted: "NETTED ACROSS EVERY OPEN TAB",
    you: "YOU",
    to: "to",
  },
  share: {
    message: "Message",
    preview: "GROUP CHAT PREVIEW",
  },
  sheet: {
    close: "Close",
    dragHandle: "Drag down to close",
  },
  dialog: {
    cancel: "Not yet",
  },
  toast: {
    undo: "Undo",
    dismiss: "Dismiss",
  },
  skeleton: {
    loading: "Loading",
  },
  dev: {
    title: "Components",
    kicker: "DEV · KITCHENTAB",
    lightPanel: "Light theme",
    darkPanel: "Dark theme",
    openSheet: "Open sheet",
    openDialog: "Open dialog",
    openDangerDialog: "Open danger dialog",
    showToast: "Show toast",
  },
  stamp: {
    settled: (date: string) => `SETTLED ${date}`,
    voided: "VOIDED",
  },
} as const;

export type Strings = typeof strings;
