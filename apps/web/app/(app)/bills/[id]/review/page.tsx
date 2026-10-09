import type { Metadata } from "next";
import { strings } from "@/lib/strings";
import { ReviewRoute } from "./ReviewScreen";

export const metadata: Metadata = { title: strings.review.title };

export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReviewRoute id={id} />;
}
