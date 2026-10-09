import type { Metadata } from "next";
import { strings } from "@/lib/strings";
import { SendRoute } from "./SendScreen";

export const metadata: Metadata = { title: strings.send.title };

export default async function SendPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SendRoute id={id} />;
}
