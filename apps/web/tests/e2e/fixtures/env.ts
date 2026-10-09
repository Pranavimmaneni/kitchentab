/**
 * The API the e2e suite runs against (TEST_PLAN §2): test inbox for email codes, fake SMS, test
 * routes on, its own database (`kitchentab_test`) and Redis db 1. The inbox secret is a fixed
 * test-only value; it guards nothing outside this local test server.
 */
export const API_PORT = Number(process.env.KT_E2E_API_PORT ?? 8010);
export const API_ORIGIN = `http://localhost:${API_PORT}`;
export const TEST_INBOX_SECRET =
  process.env.KT_E2E_INBOX_SECRET ?? "e2e-inbox-secret-0123456789abcdef0123456789abcdef0123456789abcde";

export const API_ENV: Record<string, string> = {
  APP_ENV: "test",
  TEST_ROUTES_ENABLED: "true",
  EMAIL_PROVIDER: "testinbox",
  TEST_INBOX_SECRET,
  SMS_PROVIDER: "fake",
  PROFILE: "local",
  DATABASE_URL: "postgresql+asyncpg://kitchentab:kitchentab@localhost:5432/kitchentab_test",
  REDIS_URL: "redis://localhost:6379/1",
  WEB_ORIGIN: "http://localhost:3000",
  PUBLIC_LINK_BASE: "http://localhost:3000",
};
