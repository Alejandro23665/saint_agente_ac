#!/bin/bash
# Crea la base de datos de Evolution API con su propio usuario.
# Así Evolution (código de terceros) no tiene acceso a las tablas de Saint.
# Corre solo la primera vez que se crea el volumen de PostgreSQL.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${EVOLUTION_DB_USER}') THEN
      CREATE ROLE ${EVOLUTION_DB_USER} LOGIN PASSWORD '${EVOLUTION_DB_PASSWORD}' NOSUPERUSER;
    END IF;
  END
  \$\$;
EOSQL

# CREATE DATABASE no puede ir dentro de un bloque DO.
if ! psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tAc \
    "SELECT 1 FROM pg_database WHERE datname = 'evolution'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "CREATE DATABASE evolution OWNER ${EVOLUTION_DB_USER}"
fi
# Nadie más puede conectarse a la base de Evolution.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c "REVOKE ALL ON DATABASE evolution FROM PUBLIC"
