# ADR-0006: Same-origin deployment

Supersedes the kickoff plan of an API on `api.<domain>`. Per BUILD_ALL C2 the Next.js app and the
FastAPI app are two Vercel Services in one project behind one domain, with top-level rewrites
sending `/api/*` to FastAPI. Cookies are first-party (`SameSite=Lax`, `HttpOnly`, `Secure`), CORS
is not needed in production, and CSRF stays: double-submit token plus an Origin check on every
state-changing request. Locally `next dev` rewrites `/api/*` to uvicorn so the browser also sees
one origin.
