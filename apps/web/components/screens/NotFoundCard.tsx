import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { strings } from "@/lib/strings";

export type NotFoundCardProps = {
  body?: string;
  /** The card is the whole page (link pages): its heading is the page's h1. */
  pageTitle?: boolean;
};

/** "Not found / expired" (PRD §14A): bad or rotated link. */
export function NotFoundCard({ body = strings.notFound.bill, pageTitle = false }: NotFoundCardProps) {
  return (
    <ReceiptCard heading={strings.notFound.heading} headingLevel={pageTitle ? 1 : 2} hideHeading rotated>
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
