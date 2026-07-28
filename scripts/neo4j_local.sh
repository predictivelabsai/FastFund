#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Self-contained, user-owned Neo4j for FastFund local dev.
#
# Uses the system-installed Neo4j binaries (/usr/bin/neo4j) but runs them as the
# current user against a private home directory — no sudo, no touching the
# packaged /var/lib/neo4j instance. Mirrors AuraDB: auth is enabled, so moving
# to Aura later is just a NEO4J_URI + NEO4J_PASSWORD change in .env.
#
# Usage:
#   scripts/neo4j_local.sh setup     # one-time: build conf + set initial password
#   scripts/neo4j_local.sh start     # start in background
#   scripts/neo4j_local.sh console   # start in foreground (Ctrl-C to stop)
#   scripts/neo4j_local.sh stop
#   scripts/neo4j_local.sh status
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

export NEO4J_HOME="${FASTFUND_NEO4J_HOME:-$HOME/.fastfund-neo4j}"
export NEO4J_CONF="$NEO4J_HOME/conf"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-fastfund-dev-password}"
SRC_CONF="${FASTFUND_NEO4J_SRC_CONF:-/etc/neo4j/neo4j.conf}"

setup() {
  mkdir -p "$NEO4J_HOME"/{data,logs,run,import,plugins,conf,certificates}
  # Base the config on the packaged one (keeps the Java-11 JVM flags), then
  # redirect all writable directories into our home and enable auth.
  sed -E \
    -e "s|^dbms.directories.data=.*|dbms.directories.data=$NEO4J_HOME/data|" \
    -e "s|^dbms.directories.plugins=.*|dbms.directories.plugins=$NEO4J_HOME/plugins|" \
    -e "s|^dbms.directories.logs=.*|dbms.directories.logs=$NEO4J_HOME/logs|" \
    -e "s|^dbms.directories.run=.*|dbms.directories.run=$NEO4J_HOME/run|" \
    -e "s|^dbms.directories.import=.*|dbms.directories.import=$NEO4J_HOME/import|" \
    -e "s|^dbms.security.auth_enabled=false|dbms.security.auth_enabled=true|" \
    "$SRC_CONF" > "$NEO4J_CONF/neo4j.conf"
  cat >> "$NEO4J_CONF/neo4j.conf" <<EOF

# ── FastFund local overrides ──────────────────────────────────────────────────
dbms.connector.bolt.listen_address=:7687
dbms.connector.http.listen_address=:7474
dbms.memory.heap.initial_size=512m
dbms.memory.heap.max_size=1g
dbms.memory.pagecache.size=512m
EOF
  # Initial password must be set before first start (writes data/dbms/auth).
  neo4j-admin set-initial-password "$NEO4J_PASSWORD"
  echo "Neo4j local instance ready at $NEO4J_HOME (bolt://localhost:7687)"
}

case "${1:-}" in
  setup)   setup ;;
  start)   neo4j start ;;
  console) neo4j console ;;
  stop)    neo4j stop ;;
  status)  neo4j status ;;
  *) echo "usage: $0 {setup|start|console|stop|status}"; exit 1 ;;
esac
