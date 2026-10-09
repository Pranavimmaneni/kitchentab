# ADR-0016: Day-one sign-in (BUILD_ALL C3)

Email codes are the only sign-in method shown on day one. `EMAIL_PROVIDER`:

- `smtp`: Gmail (`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, STARTTLS, `SMTP_USER`,
  `SMTP_PASSWORD`, `EMAIL_FROM`). Set by `make set-email` in the morning.
- `testinbox`: messages are stored in Postgres and readable only at `GET /api/test/inbox` with
  `X-Test-Secret: $TEST_INBOX_SECRET` (at least 64 chars, compared in constant time). Allowed in
  production until `make set-email` runs; boot fails if the secret is missing or short. This is not
  a `/__test/*` route: it is a secret-guarded production feature, and SEC-11 still holds.
- `fake`: SMTP to Mailpit (local and tests).

Phone codes (Twilio) and Google sign-in stay implemented and tested with fakes; their buttons are
hidden unless their keys are configured (`GET /api/v1/auth/methods`). Turnstile is required only
when phone codes are enabled.
