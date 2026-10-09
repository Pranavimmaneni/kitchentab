-- Runs once when the local Postgres volume is created. The test database is used by
-- `make test` when TEST_DATABASE_URL points at the compose stack instead of Testcontainers.
CREATE DATABASE kitchentab_test OWNER kitchentab;
