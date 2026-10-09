import type { Metadata } from "next";
import { VerifyScreen } from "./VerifyScreen";

export const metadata: Metadata = { title: "Enter code", robots: { index: false } };

export default function VerifyPage() {
  return <VerifyScreen />;
}
