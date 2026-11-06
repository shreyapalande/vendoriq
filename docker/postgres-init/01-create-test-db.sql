-- Runs once, when the PostgreSQL data volume is first initialized.
-- The tests use this separate database (see TEST_DATABASE_URL), never the dev database.
-- If the volume already existed before this file was added: `docker compose down -v`.
CREATE DATABASE vendoriq_test;
