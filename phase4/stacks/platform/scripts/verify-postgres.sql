\set ECHO none
\set ECHO_HIDDEN off
\set VERBOSITY sqlstate
\set ON_ERROR_STOP on
\getenv auth_db AUTH_DB_NAME
\getenv auth_user AUTH_DB_USER
\getenv auth_migration_user AUTH_MIGRATION_DB_USER
\getenv os_db OS_DB_NAME
\getenv os_user OS_DB_USER
\getenv billing_db BILLING_DB_NAME
\getenv billing_user BILLING_DB_USER
SET search_path = pg_catalog;
SET default_transaction_read_only = on;
SELECT count(*) = 3 AND bool_and(owner_role.rolname = expected.owner_name)
AS owners_ready
FROM (VALUES (:'auth_db', :'auth_migration_user'), (:'os_db', :'os_user'), (:'billing_db', :'billing_user')) AS expected(database_name, owner_name)
JOIN pg_database actual ON actual.datname = expected.database_name
JOIN pg_roles owner_role ON owner_role.oid = actual.datdba \gset
\if :owners_ready
\else
DO $$ BEGIN RAISE EXCEPTION 'Database ownership verification failed'; END $$;
\endif
SELECT count(*) = 4 AND bool_and(rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
AS roles_ready FROM pg_roles WHERE rolname IN (:'auth_user', :'auth_migration_user', :'os_user', :'billing_user') \gset
\if :roles_ready
\else
DO $$ BEGIN RAISE EXCEPTION 'Role verification failed'; END $$;
\endif
SELECT bool_and(has_database_privilege(access_role, database_name, 'CONNECT') = allowed)
AS isolation_ready FROM (VALUES
  (:'auth_user', :'auth_db', true), (:'auth_user', :'os_db', false), (:'auth_user', :'billing_db', false),
  (:'auth_migration_user', :'auth_db', true), (:'auth_migration_user', :'os_db', false), (:'auth_migration_user', :'billing_db', false),
  (:'os_user', :'auth_db', false), (:'os_user', :'os_db', true), (:'os_user', :'billing_db', false),
  (:'billing_user', :'auth_db', false), (:'billing_user', :'os_db', false), (:'billing_user', :'billing_db', true)
) AS expected(access_role, database_name, allowed) \gset
\if :isolation_ready
\else
DO $$ BEGIN RAISE EXCEPTION 'Database isolation verification failed'; END $$;
\endif
\connect :auth_db
SET search_path = pg_catalog;
SET default_transaction_read_only = on;
SELECT has_schema_privilege(:'auth_user', 'public', 'USAGE')
AND NOT has_schema_privilege(:'auth_user', 'public', 'CREATE')
AND NOT pg_has_role(:'auth_user', :'auth_migration_user', 'MEMBER')
AND has_schema_privilege(:'auth_migration_user', 'public', 'CREATE') AS schema_ready \gset
\if :schema_ready
\else
DO $$ BEGIN RAISE EXCEPTION 'Auth schema verification failed'; END $$;
\endif
SELECT bool_and(has_table_privilege(:'auth_user', table_name, required_privilege))
AS tables_ready FROM (VALUES ('public.usuarios'), ('public.usuario_papeis'), ('public.usuario_clientes')) AS expected(table_name)
CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) AS required(required_privilege) \gset
\if :tables_ready
\else
DO $$ BEGIN RAISE EXCEPTION 'Auth DML verification failed'; END $$;
\endif
SELECT count(*) FILTER (WHERE version IN ('1', '2') AND success) = 2
AND count(*) FILTER (WHERE NOT success) = 0 AS migrations_ready FROM public.flyway_schema_history \gset
\if :migrations_ready
\else
DO $$ BEGIN RAISE EXCEPTION 'Auth migration verification failed'; END $$;
\endif
\echo POSTGRES_POSTCONDITIONS_VERIFIED
