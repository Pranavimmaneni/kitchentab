import { BillRoute } from "./BillScreen";

export default async function BillPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BillRoute id={id} />;
}
