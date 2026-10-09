import type { Metadata } from "next";
import { strings } from "@/lib/strings";
import { ShareRoute } from "./ShareScreen";

export const metadata: Metadata = { title: strings.shareScreen.title };

export default async function SharePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ShareRoute id={id} />;
}
