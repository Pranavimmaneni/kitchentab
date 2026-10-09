import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { JoinScreen } from "@/components/screens/JoinScreen";
import { LinkShell } from "@/components/screens/LinkShell";
import { NotFoundCard } from "@/components/screens/NotFoundCard";
import { LINK_METADATA, loadInvite } from "@/lib/link-pages";
import { serverMethods } from "@/lib/server-auth";
import { strings } from "@/lib/strings";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ token: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const loaded = await loadInvite((await params).token);
  if (!loaded.ok) return { ...LINK_METADATA, title: strings.notFound.heading };
  const title = strings.join.inviteHeadline(loaded.data.admin_name ?? strings.join.someone, loaded.data.house.name);
  return { ...LINK_METADATA, title, openGraph: { title } };
}

/** D · Join from an invite link (PRD §14A). */
export default async function InviteLinkPage({ params }: Props) {
  const { token } = await params;
  const loaded = await loadInvite(token);
  if (!loaded.ok) {
    return (
      <LinkShell>
        {loaded.expired ? <NotFoundCard body={strings.notFound.invite} /> : <ErrorCard error={loaded.error} />}
      </LinkShell>
    );
  }
  const view = loaded.data;
  if (view.my_member) redirect("/");
  const methods = await serverMethods();
  return (
    <JoinScreen
      kind="invite"
      token={token}
      headline={strings.join.inviteHeadline(view.admin_name ?? strings.join.someone, view.house.name)}
      preview={{ kind: "invite", house: view.house.name, meta: strings.join.memberCount(view.member_count) }}
      placeholders={view.placeholders}
      signedIn={view.signed_in}
      askName={view.admin_name}
      doneHref="/"
      methods={methods}
    />
  );
}
