import { strings } from "@/lib/strings";

/**
 * "Continue with Google" (54 px, full width). A plain link to the OIDC start endpoint; only
 * rendered when `GET /auth/methods` reports Google is configured (BUILD_ALL C3).
 */
export function GoogleButton({ returnTo, onClick }: { returnTo?: string; onClick?: () => void }) {
  const href = `/api/v1/auth/google/start?return_to=${encodeURIComponent(returnTo ?? "/")}`;
  return (
    <a href={href} className="kt-pill kt-pill--google" data-size="52" data-full="" onClick={onClick}>
      <span className="kt-google-g" aria-hidden="true">
        G
      </span>
      <span>{strings.start.google}</span>
    </a>
  );
}
