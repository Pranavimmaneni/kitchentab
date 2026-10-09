import { AppHeader } from "@/components/AppHeader";
import { BottomNav } from "@/components/BottomNav";
import { strings } from "@/lib/strings";

/** Placeholder Kitchen (K1 arrives in Milestone 8). */
export default function KitchenPage() {
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col">
      <AppHeader large stepLabel={strings.app.name.toUpperCase()} title={strings.kitchen.title} />
      <main className="flex-1 px-5 pb-6">
        <p className="m-0 text-[15px] text-muted">{strings.kitchen.comingSoon}</p>
      </main>
      <BottomNav />
    </div>
  );
}
