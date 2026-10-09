import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { strings } from "@/lib/strings";

export type NotFoundCardProps = { body?: string };

/** "Not found / expired" (PRD §14A): bad or rotated link. */
export function NotFoundCard({ body = strings.notFound.bill }: NotFoundCardProps) {
  return (
    <ReceiptCard heading={strings.notFound.heading} hideHeading rotated>
      <div className="kt-not-found">
        <p className="kt-not-found__title" aria-hidden="true">
          {strings.notFound.heading}
        </p>
        <p className="kt-not-found__body">{body}</p>
        <PillButton href="/" variant="ink" fullWidth>
          {strings.notFound.home}
        </PillButton>
      </div>
    </ReceiptCard>
  );
}
