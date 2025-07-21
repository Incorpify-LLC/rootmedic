import requests
import datetime
import json

# CONFIGURATION
LOKI_URL = "http://localhost:3100/loki/api/v1/query_range"
QUERY = '{job="systemd-journal"} |= "error" or |= "warning"'
LIMIT = 100
DURATION = "1h"  # logs from the past hour

def fetch_logs():
    now = datetime.datetime.utcnow()
    end = int(now.timestamp() * 1e9)
    start = int((now - datetime.timedelta(hours=1)).timestamp() * 1e9)

    params = {
        "query": QUERY,
        "limit": LIMIT,
        "start": start,
        "end": end,
        "direction": "backward"
    }

    print(f"Querying Loki for logs between {start} and {end}...")

    try:
        response = requests.get(LOKI_URL, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get('data', {}).get('result', [])
    except Exception as e:
        print(f"Error querying Loki: {e}")
        return []

def parse_and_normalize(logs):
    events = []
    for stream in logs:
        for entry in stream.get('values', []):
            ts, raw_message = entry
            msg = raw_message.strip()
            events.append({
                "timestamp": datetime.datetime.fromtimestamp(int(ts) / 1e9).isoformat(),
                "host": stream.get('stream', {}).get('host', 'unknown'),
                "unit": stream.get('stream', {}).get('systemd_unit', 'unknown'),
                "message": msg
            })
    return events

def main():
    logs = fetch_logs()
    parsed = parse_and_normalize(logs)
    print(json.dumps(parsed, indent=2))

if __name__ == "__main__":
    main()
