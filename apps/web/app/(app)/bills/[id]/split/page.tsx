import type { Metadata } from "next";
import { strings } from "@/lib/strings";
import { SplitRoute } from "./SplitScreen";

export const metadata: Metadata = { title: strings.split.title };

export default async function SplitPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SplitRoute id={id} />;
}
