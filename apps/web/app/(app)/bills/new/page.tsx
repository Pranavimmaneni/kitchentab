import type { Metadata } from "next";
import { CaptureScreen } from "./CaptureScreen";

export const metadata: Metadata = { title: "Snap a receipt" };

type Props = { searchParams: Promise<{ house?: string }> };

export default async function NewBillPage({ searchParams }: Props) {
  const { house } = await searchParams;
  return <CaptureScreen houseParam={typeof house === "string" ? house : null} />;
}
