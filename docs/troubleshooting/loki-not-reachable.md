# Troubleshooting: Loki Not Reachable

**Installer error**: `Loki is not reachable at http://...`

RootMedic checks `GET <loki_url>/ready` and expects HTTP 200. This page explains why that can fail and how to fix it.

---

## Option A — Start the bundled Docker stack

The installer offers to start the bundled Loki + Fluent Bit + Grafana stack automatically. If you said no, or if it failed, run it manually:

```bash
# Docker
docker compose -f /opt/rootmedic/Deployment/docker-compose.yml up -d

# Podman
podman-compose -f /opt/rootmedic/Deployment/docker-compose.yml up -d
```

Verify Loki is up:

```bash
curl -s http://localhost:3100/ready
# Expected output: ready
```

Then re-run the installer.

---

## Option B — Loki is on another machine (LAN)

If Loki runs on a different machine (e.g., `192.168.1.10`), the installer's Loki URL prompt accepts any URL:

```
http://192.168.1.10:3100
```

Make sure:
1. Port 3100 is open in the remote machine's firewall: `sudo ufw allow 3100/tcp`
2. Loki's config (`loki-config.yaml`) has `http_listen_address: 0.0.0.0` (not `127.0.0.1`)
3. You can reach it: `curl http://192.168.1.10:3100/ready`

---

## Option C — Install Docker if not present

If you chose to start the bundled stack but Docker/Podman wasn't installed:

**Docker (Debian/Ubuntu):**
```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
```
Reference: https://docs.docker.com/engine/install/

**Podman (Debian/Ubuntu):**
```bash
sudo apt-get install -y podman podman-compose
```

---

## Option D — Install Loki natively (no Docker)

Download the Loki binary and run it as a systemd service:

```bash
# Download latest Loki binary
LOKI_VERSION=2.9.0
curl -fsSL "https://github.com/grafana/loki/releases/download/v${LOKI_VERSION}/loki-linux-amd64.zip" \
  -o /tmp/loki.zip
unzip /tmp/loki.zip -d /usr/local/bin/
chmod +x /usr/local/bin/loki-linux-amd64
ln -sf /usr/local/bin/loki-linux-amd64 /usr/local/bin/loki

# Create a minimal config
mkdir -p /etc/loki /var/loki
cp /opt/rootmedic/Deployment/loki-config.yaml /etc/loki/config.yaml

# Create systemd unit
cat > /etc/systemd/system/loki.service <<'EOF'
[Unit]
Description=Loki log aggregation
After=network.target

[Service]
ExecStart=/usr/local/bin/loki -config.file=/etc/loki/config.yaml
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now loki
```

Verify: `curl -s http://localhost:3100/ready`

---

## Diagnosing the problem

```bash
# Is anything listening on 3100?
ss -tlnp | grep 3100

# If using Docker: is the container running?
docker ps | grep loki

# If using Docker: check container logs
docker logs loki --tail 30

# Check Loki config for listen address
grep http_listen /etc/loki/config.yaml
```

---

## After fixing

Re-run the installer from the beginning:

```bash
sudo bash /opt/rootmedic/install.sh
```

Or if you only want to re-run the verification:

```bash
curl -s http://localhost:3100/ready
```
