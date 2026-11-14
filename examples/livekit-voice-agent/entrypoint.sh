#!/bin/sh
# Container entrypoint: `agent` (default) runs the LiveKit worker, `token` the token server.
set -eu
case "${1:-agent}" in
  agent) exec python agent.py start ;;
  token) exec python token_server.py ;;
  *) exec "$@" ;;
esac
