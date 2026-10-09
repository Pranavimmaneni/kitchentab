import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { LinkShell } from "@/components/screens/LinkShell";
import { JoinScreen } from "@/components/screens/JoinScreen";
import { NotFoundCard } from "@/components/screens/NotFoundCard";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { shortDate } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import { billLinkMetadata, loadBillLink } from "@/lib/link-pages";
import { serverMethods } from "@/lib/server-auth";
import { strings } from "@/lib/strings";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ token: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return billLinkMetadata((await params).token);
}

/** D · Join from a bill link (PRD §14A): read-only peek + "Which one are you?". */
export default async function BillLinkPage({ params }: Props) {
  const { token } = await params;
  const loaded = await loadBillLink(token);
  if (!loaded.ok) {
    return (
      <LinkShell>
        {loaded.expired ? <NotFoundCard body={strings.notFound.bill} /> : <ErrorCard error={loaded.error} />}
      </LinkShell>
    );
  }
  const view = loaded.data;
  // Signed in and already a member: skip to the bill.
  if (view.join.my_member) redirect(`/bills/${view.bill.id}`);
  const methods = await serverMethods();
  const store = view.bill.store_name ?? strings.bill.untitled;
  const payer = view.payer_name ?? strings.join.someone;
  return (
    <JoinScreen
      kind="bill"
      token={token}
      headline={strings.join.billHeadline(payer, view.bill.store_name ?? strings.join.aStore)}
      preview={{
        kind: "bill",
        store,
        totalText: formatMoney(view.bill.total_cents, view.bill.currency),
        meta: `${shortDate(view.bill.purchase_date)} · ${strings.join.items(view.items.length)} · ${view.house.name}`,
      }}
      placeholders={view.join.placeholders}
      signedIn={view.join.signed_in}
      askName={view.payer_name}
      doneHref={`/bills/${view.bill.id}`}
      methods={methods}
    />
  );
}
