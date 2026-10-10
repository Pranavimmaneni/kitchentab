import type { InventoryItem, ItemOwner, ShareRequest } from "../../../lib/api/kitchen-types";

export const ME = "m-pranav";
export const PEOPLE: Record<string, ItemOwner> = {
  pranav: { id: ME, display_name: "Pranav", initial: "P", color: "#C6F432", open_to_share: false },
  arjun: {
    id: "m-arjun",
    display_name: "Arjun",
    initial: "A",
    color: "#6A4CFF",
    open_to_share: false,
  },
  meera: {
    id: "m-meera",
    display_name: "Meera",
    initial: "M",
    color: "#FF8A65",
    open_to_share: false,
  },
  kabir: {
    id: "m-kabir",
    display_name: "Kabir",
    initial: "K",
    color: "#7FD3F7",
    open_to_share: false,
  },
};

export function owner(name: keyof typeof PEOPLE, open = false): ItemOwner {
  return { ...PEOPLE[name]!, open_to_share: open };
}

/** An in-stock item as `GET /houses/{id}/inventory` returns it (services/inventory.py item_views). */
export function item(over: Partial<InventoryItem> = {}): InventoryItem {
  const owners = over.owners ?? [owner("pranav"), owner("arjun")];
  const isOpen = owners.every((o) => o.open_to_share);
  return {
    id: "inv-chicken",
    name: "Chicken thighs 3 lb",
    short_name: "Chicken thighs",
    icon_key: "chicken",
    place: "fridge",
    category: "meat_fish",
    price_cents: 1137,
    stocked_on: "2026-10-02",
    shelf_life_days: 2,
    status: "in_stock",
    version: 1,
    source: {
      bill_id: "b-shoprite",
      store_name: "ShopRite",
      purchase_date: "2026-10-02",
      state: "open",
    },
    owners,
    holdouts: owners.filter((o) => !o.open_to_share).map((o) => o.display_name),
    is_open: isOpen,
    open_for_me: isOpen || owners.some((o) => o.id === ME),
    granted_to_me: false,
    granted_by: null,
    mine: owners.some((o) => o.id === ME),
    use_soon: false,
    probably_gone: false,
    days_left: 5,
    soon_label: null,
    my_request: null,
    ...over,
  };
}

export function request(over: Partial<ShareRequest> = {}): ShareRequest {
  return {
    id: "req-1",
    item: {
      id: "inv-chicken",
      name: "Chicken thighs 3 lb",
      short_name: "Chicken thighs",
      icon_key: "chicken",
      bill_id: "b-shoprite",
    },
    requester: { id: "m-kabir", display_name: "Kabir", initial: "K", color: "#7FD3F7" },
    amount: "a_bit",
    amount_words: "a bit of",
    note: "For tonight's curry",
    status: "pending",
    resolution: null,
    chip_in_cents: null,
    answered_by: null,
    answered_at: null,
    undo_until: null,
    expires_at: "2026-10-05T19:00:00Z",
    created_at: "2026-10-03T18:48:00Z",
    version: 1,
    owners: ["Pranav", "Arjun"],
    source: { store_name: "ShopRite", state: "open" },
    line_total_cents: 1137,
    options: ["free", "split"],
    split_deltas: { "m:m-kabir": 383, "m:m-pranav": -192, "m:m-arjun": -191 },
    chip_in_suggestion_cents: null,
    co_owners: ["Arjun"],
    ...over,
  };
}
