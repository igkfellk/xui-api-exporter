# 3X-UI API Exporter

[![Docker Pulls](https://img.shields.io/badge/docker-ghcr.io-blue)](https://github.com/users/igkfellk/packages/container/package/xui-api-exporter)

Prometheus exporter for **3X-UI panel** metrics. Collects client traffic, online status, inbound statistics, and system health through the official 3X-UI REST API using Bearer token authentication.

## 🚀 Features

- **Client metrics** — upload/download bytes, online status, expiration time per email
- **Inbound metrics** — traffic per inbound with protocol labels
- **System metrics** — CPU, memory, disk, network I/O, Xray state
- **Bearer token authentication** — no session cookies, secure and simple
- **Lightweight** — Python-based, runs in a small Docker container

## 📊 Available Metrics

| Metric | Description | Labels |
|--------|-------------|--------|
| `xui_client_up_bytes_total` | Upload bytes per client | `email` |
| `xui_client_down_bytes_total` | Download bytes per client | `email` |
| `xui_client_online` | Online status (1 = online, 0 = offline) | `email` |
| `xui_client_expire_timestamp` | Expiration timestamp (Unix seconds) | `email` |
| `xui_inbound_up_bytes_total` | Upload bytes per inbound | `remark`, `protocol` |
| `xui_inbound_down_bytes_total` | Download bytes per inbound | `remark`, `protocol` |
| `xui_system_cpu_usage_percent` | CPU usage percentage | — |
| `xui_system_memory_used_bytes` | Used RAM in bytes | — |
| `xui_system_memory_total_bytes` | Total RAM in bytes | — |
| `xui_system_disk_used_bytes` | Used disk space in bytes | — |
| `xui_system_disk_total_bytes` | Total disk space in bytes | — |
| `xui_xray_state` | Xray state (1 = running, 0 = stopped) | — |
| `xui_total_clients` | Total number of clients | — |
| `xui_active_clients` | Number of active (enabled) clients | — |
| `xui_online_clients` | Number of online clients | — |

## 🐳 Quick Start (Docker Compose)

### 1. Create API token in 3X-UI

Go to your 3X-UI panel → **Settings** → **Security** → **API Tokens** → **Create Token**

### 2. Add to your `docker-compose.yml`

```yaml
services:
  xui-api-exporter:
    image: ghcr.io/igkfellk/xui-api-exporter:latest
    container_name: xui-api-exporter
    restart: unless-stopped
    environment:
      - XUI_URL=http://xui:2053           # Internal URL of 3X-UI panel
      - XUI_API_TOKEN=${XUI_API_TOKEN}    # Your API token from step 1
    networks:
      - vpn-net
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
