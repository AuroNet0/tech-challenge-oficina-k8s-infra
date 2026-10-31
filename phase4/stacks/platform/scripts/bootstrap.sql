\set ECHO none
\set ECHO_HIDDEN off
\set VERBOSITY sqlstate
\set ON_ERROR_STOP on
\getenv auth_db AUTH_DB_NAME
\getenv auth_user AUTH_DB_USER
\getenv auth_password AUTH_DB_PASSWORD
\getenv os_db OS_DB_NAME
\getenv os_user OS_DB_USER
\getenv os_password OS_DB_PASSWORD
\getenv billing_db BILLING_DB_NAME
\getenv billing_user BILLING_DB_USER
\getenv billing_password BILLING_DB_PASSWORD
SET search_path = pg_catalog;
SELECT pg_advisory_lock(hashtextextended('oficina-phase4-database-bootstrap', 0));
SELECT NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname IN (:'auth_user', :'os_user', :'billing_user')
  AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
) AND NOT EXISTS (
  SELECT 1 FROM pg_auth_members memberships JOIN pg_roles members ON members.oid = memberships.member
  WHERE members.rolname IN (:'auth_user', :'os_user', :'billing_user')
) AS roles_safe \gset
\if :roles_safe
\else
DO $$ BEGIN RAISE EXCEPTION 'Unsafe application roles'; END $$;
\endif
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'auth_user', :'auth_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'auth_user') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'os_user', :'os_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'os_user') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'billing_user', :'billing_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'billing_user') \gexec
\unset auth_password
\unset os_password
\unset billing_password
SELECT format('GRANT %I TO %I', application_role, current_user)
FROM (VALUES (:'auth_user'), (:'os_user'), (:'billing_user')) AS roles(application_role) \gexec
SELECT NOT EXISTS (
  SELECT 1 FROM (VALUES (:'auth_db', :'auth_user'), (:'os_db', :'os_user'), (:'billing_db', :'billing_user')) AS expected(database_name, owner_name)
  JOIN pg_database actual ON actual.datname = expected.database_name
  JOIN pg_roles owner_role ON owner_role.oid = actual.datdba
  WHERE owner_role.rolname <> expected.owner_name
) AS ownership_safe \gset
\if :ownership_safe
\else
DO $$ BEGIN RAISE EXCEPTION 'Unexpected database ownership'; END $$;
\endif
SELECT format('CREATE DATABASE %I OWNER %I', expected.database_name, expected.owner_name)
FROM (VALUES (:'auth_db', :'auth_user'), (:'os_db', :'os_user'), (:'billing_db', :'billing_user')) AS expected(database_name, owner_name)
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = expected.database_name) \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC, %I, %I, %I', database_name, :'auth_user', :'os_user', :'billing_user')
FROM (VALUES (:'auth_db'), (:'os_db'), (:'billing_db')) AS databases(database_name) \gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', database_name, owner_name)
FROM (VALUES (:'auth_db', :'auth_user'), (:'os_db', :'os_user'), (:'billing_db', :'billing_user')) AS expected(database_name, owner_name) \gexec
SELECT pg_advisory_unlock(hashtextextended('oficina-phase4-database-bootstrap', 0));
\connect :auth_db
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"auth_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"auth_user" IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"auth_user" IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"auth_user" IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
\connect :os_db
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"os_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"os_user" IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"os_user" IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"os_user" IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
\connect :billing_db
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"billing_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"billing_user" IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"billing_user" IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"billing_user" IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
SELECT format('REVOKE %I FROM %I', application_role, current_user)
FROM (VALUES (:'auth_user'), (:'os_user'), (:'billing_user')) AS roles(application_role) \gexec
