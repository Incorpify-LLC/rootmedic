#!/usr/bin/env bash
# Stub systemctl for Docker CI — logs calls but does nothing.
echo "[systemctl-stub] $*" >&2
case "$1" in
  is-active)   exit 0 ;;   # pretend all services are active
  status)      echo "active (running)" ;;
  *)           exit 0 ;;
esac
