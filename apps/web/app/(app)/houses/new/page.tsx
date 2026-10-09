import type { Metadata } from "next";
import { HouseSetupScreen } from "./HouseSetupScreen";

export const metadata: Metadata = { title: "Who lives here?" };

export default function NewHousePage() {
  return <HouseSetupScreen />;
}
