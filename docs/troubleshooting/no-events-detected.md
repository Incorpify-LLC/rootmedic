# Troubleshooting: Agent Running but No Events Detected

The agent (`rootmedic.service`) is active but either produces no output or logs `"No error/warning events found"`.

---

## Step 1 — Check agent logs

```bash
journalctl -u rootmedic -n 50 --no-pager
```

Look for:
- `No error/warning events found` → the Loki query returned 0 results (see Step 2)
- Connection errors to Loki → see [Loki not reachable](loki-not-reachable.md)
- LLM errors → see [LLM not responding](llm-not-responding.md)

---

## Step 2 — Verify Fluent Bit is sending logs to Loki

Generate a synthetic error and check if it reaches Loki:

```bash
# Write an error to the system journal
systemd-cat -p err echo "rootmedic-test $(date)"

# Wait ~10 seconds, then query Loki
sleep 10
curl -s -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={job="fluent-bit"}' \
  --data-urlencode "start=$(date -d '2 minutes ago' +%s)000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
results = d['data']['result']
if not results:
    print('No results — Fluent Bit is not sending logs to Loki.')
else:
    for r in results:
        for ts, line in r['values']:
            print(line[:120])
"
```

If no results appear, see [Fluent Bit troubleshooting](fluent-bit.md).

---

## Step 3 — Check the Loki query window

The agent queries Loki for the last N minutes (default: last 10 minutes). If Fluent Bit was just installed, logs from before installation will not appear. Generate a new error and wait for the next agent poll cycle.

Check how often the agent polls:
```bash
grep -i interval /etc/rootmedic/config.yaml || echo "(using default: every run)"
```

---

## Step 4 — Check the priority filter

Fluent Bit only forwards priority 3 (err) and priority 4 (warning) journal entries. Info-level logs are intentionally dropped.

Verify priority filter is working correctly:
```bash
# Generate a warning (priority 4)
systemd-cat -p warning echo "test-warning $(date)"

# Generate an error (priority 3)
systemd-cat -p err echo "test-error $(date)"
```

If you need to forward all priorities for debugging, temporarily remove the `[FILTER]` section from `/etc/fluent-bit/fluent-bit.conf` and restart Fluent Bit.

---

## Step 5 — Check Loki labels

The agent queries Loki using labels like `{job="fluent-bit"}` or `{job="systemd-journal"}`. If Fluent Bit is using different labels, queries return empty results.

Check what labels are actually in Loki:

```bash
curl -s "http://localhost:3100/loki/api/v1/labels" | python3 -m json.tool
curl -s "http://localhost:3100/loki/api/v1/label/job/values" | python3 -m json.tool
```

If the `job` value differs, update the Loki query in the agent config or the `Labels` line in `/etc/fluent-bit/fluent-bit.conf`.
