#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'clinicpass';
    ALTER SYSTEM SET wal_level = replica;
    ALTER SYSTEM SET max_wal_senders = 3;
    ALTER SYSTEM SET wal_keep_size = 256;
EOSQL

# Add pg_hba.conf entry for replication connections
PG_HBA="${PGDATA:-/var/lib/postgresql/data}/pg_hba.conf"
if [ -f "$PG_HBA" ]; then
    echo "host replication replicator 0.0.0.0/0 trust" >> "$PG_HBA"
fi
