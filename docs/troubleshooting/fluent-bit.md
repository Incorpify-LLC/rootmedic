# Troubleshooting: Fluent Bit

Fluent Bit is the log collector that reads your system journal and forwards error/warning entries to Loki. If it's not running or not sending logs, the RootMedic agent will see no events.

---

## Check if Fluent Bit is running

```bash
systemctl status fluent-bit
journalctl -u fluent-bit --since "5 minutes ago"
```

Common output indicating it works:
```
[2024/01/01 12:00:00] [ info] [output:loki:loki.0] worker #0 started
```

---

## Fluent Bit is not installed

The installer handles Debian/Ubuntu and RHEL/CentOS automatically. For other distros:

**Arch Linux:**
```bash
yay -S fluent-bit
```

**Alpine:**
```bash
apk add fluent-bit
```

**Manual binary install (any distro):**
```bash
FB_VERSION=3.3.0
curl -fsSL "https://github.com/fluent/fluent-bit/releases/download/v${FB_VERSION}/fluent-bit-${FB_VERSION}-linux-x86_64.tar.gz" \
  | tar xz -C /usr/local/bin --strip-components=1 --wildcards "*/fluent-bit"
chmod +x /usr/local/bin/fluent-bit
```

---

## Fluent Bit cannot connect to Loki

Symptoms in `journalctl -u fluent-bit`:
```
[error] [output:loki:loki.0] http_do=failed
```

1. Verify Loki is running: `curl http://localhost:3100/ready`
2. Check the host/port in `/etc/fluent-bit/fluent-bit.conf` under `[OUTPUT]`
3. Check firewall: `curl http://<loki-host>:3100/ready`

---

## Fluent Bit is running but no logs appear in Loki

### Check the priority filter

The config keeps only priority 3 (error) and priority 4 (warning). If your system is healthy, there may genuinely be no such events.

Generate a test error to verify the pipeline:

```bash
# Generate a systemd journal error entry
systemd-cat -p err echo "rootmedic-test-error $(date)"

# Query Loki for it (wait ~10 seconds for Fluent Bit flush)
curl -s -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={job="fluent-bit"}' \
  --data-urlencode "start=$(date -d '1 minute ago' +%s)000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); \
    [print(v[1]) for r in d['data']['result'] for v in r['values']]"
```

If the test entry appears in Loki, the pipeline works.

### Temporarily remove the priority filter to confirm

Edit `/etc/fluent-bit/fluent-bit.conf`, comment out the `[FILTER]` section, restart Fluent Bit, and check if logs flow. Re-enable the filter once confirmed working.

---

## View the Fluent Bit config

```bash
cat /etc/fluent-bit/fluent-bit.conf
```

The installer writes this config. If you need to change the Loki host:

```bash
nano /etc/fluent-bit/fluent-bit.conf
systemctl restart fluent-bit
```

---

## Fluent Bit package repo not available

If `apt-get install fluent-bit` fails with "Unable to find package":

```bash
# Re-add the Fluent Bit apt repo manually
curl -fsSL https://packages.fluentbit.io/fluentbit.key \
  | gpg --dearmor -o /usr/share/keyrings/fluentbit-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/fluentbit-keyring.gpg] \
  https://packages.fluentbit.io/debian/$(lsb_release -cs) stable main" \
  > /etc/apt/sources.list.d/fluentbit.list

apt-get update && apt-get install -y fluent-bit
```

Official install guide: https://docs.fluentbit.io/manual/installation/linux
