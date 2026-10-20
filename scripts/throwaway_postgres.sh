#!/usr/bin/env bash
set -euo pipefail

PG_BIN="${PG_BIN:-/c/Program Files/PostgreSQL/18/bin}"
PG_DIR="${PG_DIR:-${TMPDIR:-/tmp}/dota_meta_throwaway_pg}"
PG_PORT="${PG_PORT:-55432}"
PG_USER="${PG_USER:-dota}"
DATABASES=(dota_meta dota_meta_test)

export PATH="$PG_BIN:$PATH"

is_running() {
    pg_ctl -D "$PG_DIR" status >/dev/null 2>&1
}

start() {
    if [ ! -d "$PG_DIR" ]; then
        initdb -D "$PG_DIR" -U "$PG_USER" --auth=trust -E UTF8 >/dev/null
    fi
    if ! is_running; then
        pg_ctl -D "$PG_DIR" -o "-p $PG_PORT" -l "$PG_DIR/server.log" -w start >/dev/null
    fi
    for database in "${DATABASES[@]}"; do
        exists=$(psql -h localhost -p "$PG_PORT" -U "$PG_USER" -d postgres -tAc \
            "SELECT 1 FROM pg_database WHERE datname = '$database'")
        if [ "$exists" != "1" ]; then
            createdb -h localhost -p "$PG_PORT" -U "$PG_USER" "$database"
        fi
    done
    echo "Postgres running on localhost:$PG_PORT with databases: ${DATABASES[*]}"
    environment
}

stop() {
    if is_running; then
        pg_ctl -D "$PG_DIR" -m fast stop >/dev/null
    fi
    echo "Stopped"
}

reset() {
    stop
    rm -rf "$PG_DIR"
    echo "Removed $PG_DIR"
}

environment() {
    echo "export POSTGRES_USER=$PG_USER POSTGRES_PASSWORD=unused POSTGRES_HOST=localhost POSTGRES_PORT=$PG_PORT"
    echo "export POSTGRES_DB=dota_meta        # or dota_meta_test for the database tests"
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    reset) reset ;;
    env) environment ;;
    *) echo "usage: $0 start|stop|reset|env" >&2; exit 2 ;;
esac
