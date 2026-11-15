#!/bin/bash
# Crea el usuario con el que se conecta la aplicación.
# No es dueño de las tablas ni superusuario, así que PostgreSQL le aplica RLS.
# Los permisos sobre las tablas se los da la migración de Alembic.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${APP_DB_USER}') THEN
      CREATE ROLE ${APP_DB_USER} LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
    END IF;
  END
  \$\$;
EOSQL
