#!/usr/bin/env python3
import os
import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from prometheus_client import start_http_server, Counter, Gauge, disable_created_metrics

# ============================================================
# Отключение _created метрики
# ============================================================

disable_created_metrics()

# ============================================================
# Конфигурация
# ============================================================

XUI_URL = os.getenv('XUI_URL', 'http://xui:2053').rstrip('/')
XUI_TOKEN = os.getenv('XUI_API_TOKEN')

LISTEN_PORT = int(os.getenv('LISTEN_PORT', '9090'))
SCRAPE_INTERVAL = int(os.getenv('SCRAPE_INTERVAL', '30'))
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '10'))
ONLINE_THRESHOLD_SECONDS = int(os.getenv('ONLINE_THRESHOLD_SECONDS', '60'))

USE_PAGED_CLIENTS = os.getenv('USE_PAGED_CLIENTS', 'true').lower() in ('1', 'true', 'yes')
CLIENTS_PAGE_SIZE = min(max(int(os.getenv('CLIENTS_PAGE_SIZE', '200')), 1), 200)

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s %(levelname)s %(message)s'
)
log = logging.getLogger("xui-exporter")

# ============================================================
# HTTP session
# ============================================================

session = requests.Session()
retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
adapter = HTTPAdapter(max_retries=retry)
session.mount("http://", adapter)
session.mount("https://", adapter)

# ============================================================
# Метрики клиентов
# ============================================================

UP_BYTES = Counter('xui_client_up_bytes', 'Upload bytes per client', ['email'])
DOWN_BYTES = Counter('xui_client_down_bytes', 'Download bytes per client', ['email'])
TRAFFIC_TOTAL = Counter('xui_client_traffic_bytes', 'Total traffic per client', ['email'])

ONLINE = Gauge('xui_client_online', 'Client online status (1=online, 0=offline)', ['email'])
CLIENT_ENABLED = Gauge('xui_client_enable', 'Client enable status (1=enabled, 0=disabled)', ['email'])
CLIENT_TRAFFIC_ENABLED = Gauge('xui_client_traffic_enable', 'Client traffic accounting enable status', ['email'])
CLIENT_TOTAL_GB = Gauge('xui_client_totalgb_bytes', 'Client traffic limit in bytes', ['email'])
CLIENT_USED_BYTES = Gauge('xui_client_used_bytes', 'Client used traffic in bytes', ['email'])
CLIENT_REMAINING_BYTES = Gauge('xui_client_remaining_bytes', 'Client remaining traffic in bytes', ['email'])
CLIENT_INBOUNDS_COUNT = Gauge('xui_client_inbounds_count', 'Number of attached inbounds per client', ['email'])
EXPIRE_TIME = Gauge('xui_client_expire_timestamp', 'Client expiration timestamp', ['email'])
LAST_ONLINE = Gauge('xui_client_last_online_timestamp', 'Last online timestamp in seconds', ['email'])
CLIENT_EXPIRED = Gauge('xui_client_expired', 'Client expired status (1=expired, 0=not expired)', ['email'])

# ============================================================
# Метрики инбаундов
# ============================================================

INBOUND_UP = Counter('xui_inbound_up_bytes', 'Upload bytes per inbound', ['remark', 'protocol'])
INBOUND_DOWN = Counter('xui_inbound_down_bytes', 'Download bytes per inbound', ['remark', 'protocol'])
INBOUND_ENABLE = Gauge('xui_inbound_enable', 'Inbound enable status', ['remark', 'protocol'])

# ============================================================
# Системные метрики
# ============================================================

CPU_USAGE = Gauge('xui_system_cpu_usage_percent', 'CPU usage percentage')
MEMORY_USED = Gauge('xui_system_memory_used_bytes', 'Memory used in bytes')
MEMORY_TOTAL = Gauge('xui_system_memory_total_bytes', 'Memory total in bytes')
DISK_USED = Gauge('xui_system_disk_used_bytes', 'Disk used in bytes')
DISK_TOTAL = Gauge('xui_system_disk_total_bytes', 'Disk total in bytes')
NET_UP = Counter('xui_system_network_up_bytes', 'Network upload bytes')
NET_DOWN = Counter('xui_system_network_down_bytes', 'Network download bytes')
XRAY_STATE = Gauge('xui_xray_state', 'Xray state (1=running, 0=stopped)')
XRAY_VERSION = Gauge('xui_xray_version_info', 'Xray version info', ['version'])

# ============================================================
# Сводные метрики
# ============================================================

TOTAL_CLIENTS = Gauge('xui_total_clients', 'Total number of clients')
ACTIVE_CLIENTS = Gauge('xui_active_clients', 'Number of active clients')
ONLINE_CLIENTS = Gauge('xui_online_clients', 'Number of online clients')

# ============================================================
# Служебные метрики экспортера
# ============================================================

EXPORTER_UP = Gauge('xui_exporter_last_scrape_success', 'Whether the last scrape cycle succeeded fully (1=yes, 0=no)')
EXPORTER_LAST_SCRAPE_TS = Gauge('xui_exporter_last_scrape_timestamp', 'Unix timestamp of last scrape cycle')
EXPORTER_SCRAPE_DURATION = Gauge('xui_exporter_last_scrape_duration_seconds', 'Duration of last scrape cycle')
EXPORTER_ERRORS = Counter('xui_exporter_errors_total', 'Exporter errors total', ['stage'])

# ============================================================
# Runtime state
# ============================================================

_prev_counter_values = {}

_known_client_emails = set()
_known_inbounds = set()
_known_xray_versions = set()

# ============================================================
# Helpers
# ============================================================

def api_get(path, params=None):
    headers = {
        'Authorization': f'Bearer {XUI_TOKEN}',
        'Accept': 'application/json'
    }

    resp = session.get(
        f'{XUI_URL}{path}',
        headers=headers,
        params=params,
        timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()

    data = resp.json()
    if not data.get('success'):
        raise RuntimeError(f'API success=false for {path}: {data}')

    return data.get('obj')


def update_remote_counter(metric, cache_key, new_value):
    """
    На стороне 3X-UI значения уже абсолютные.
    Здесь превращаем их в корректный локальный Counter через delta.
    """
    try:
        new_value = int(new_value or 0)
    except (TypeError, ValueError):
        new_value = 0

    prev_value = _prev_counter_values.get(cache_key)

    if prev_value is None:
        if new_value > 0:
            metric.inc(new_value)
    elif new_value >= prev_value:
        delta = new_value - prev_value
        if delta > 0:
            metric.inc(delta)
    else:
        # Счётчик на стороне XUI мог сброситься после рестарта / reset
        if new_value > 0:
            metric.inc(new_value)

    _prev_counter_values[cache_key] = new_value


def purge_counter_cache_by_prefix_and_keypos(prefix, key_pos, valid_values):
    """
    Удаляет устаревшие ключи из кэша _prev_counter_values.
    prefix   - например 'client_' или 'inbound_'
    key_pos  - позиция ключевого label внутри tuple cache_key
    """
    stale_keys = []
    for key in _prev_counter_values.keys():
        if not isinstance(key, tuple) or not key:
            continue
        if not str(key[0]).startswith(prefix):
            continue
        if len(key) <= key_pos:
            continue
        if key[key_pos] not in valid_values:
            stale_keys.append(key)

    for key in stale_keys:
        _prev_counter_values.pop(key, None)


def safe_remove(metric, *label_values):
    try:
        metric.remove(*label_values)
    except KeyError:
        pass
    except Exception:
        pass


def fetch_clients():
    """
    Предпочитаем paged API, так как он легче и масштабируется лучше.
    Если endpoint недоступен — fallback на /clients/list.
    """
    if USE_PAGED_CLIENTS:
        try:
            items = []
            page = 1
            summary = None

            while True:
                obj = api_get('/panel/api/clients/list/paged', params={
                    'page': page,
                    'pageSize': CLIENTS_PAGE_SIZE
                }) or {}

                page_items = obj.get('items', []) or []
                items.extend(page_items)

                filtered = int(obj.get('filtered', len(page_items)) or len(page_items))
                summary = obj.get('summary') or {}

                if len(page_items) < CLIENTS_PAGE_SIZE or len(items) >= filtered:
                    break

                page += 1

            return items, summary

        except Exception as e:
            log.warning("paged clients endpoint failed, fallback to /clients/list: %s", e)

    clients = api_get('/panel/api/clients/list') or []
    return clients, None


def fetch_inbounds():
    return api_get('/panel/api/inbounds/list') or []


def fetch_status():
    return api_get('/panel/api/server/status') or {}


def touch_xray_metrics_state():
    """
    Endpoint может быть отключён — это не ошибка scrape-цикла.
    """
    try:
        api_get('/panel/api/server/xrayMetricsState')
    except Exception:
        pass


def build_last_online_map(inbounds):
    """
    lastOnline есть в clientStats внутри inbounds/list.
    Если один email встречается в нескольких inbound, берём максимальный timestamp.
    """
    result = {}

    for inbound in inbounds or []:
        for client in inbound.get('clientStats', []) or []:
            email = client.get('email')
            if not email:
                continue

            try:
                last_online_ms = int(client.get('lastOnline', 0) or 0)
            except (TypeError, ValueError):
                last_online_ms = 0

            if last_online_ms > result.get(email, 0):
                result[email] = last_online_ms

    return result


# ============================================================
# Cleanup
# ============================================================

def cleanup_client_series(current_emails):
    global _known_client_emails

    stale_emails = _known_client_emails - current_emails
    if not stale_emails:
        _known_client_emails = set(current_emails)
        return

    log.info("Cleaning up stale client series: %d", len(stale_emails))

    client_metrics = [
        UP_BYTES,
        DOWN_BYTES,
        TRAFFIC_TOTAL,
        ONLINE,
        CLIENT_ENABLED,
        CLIENT_TRAFFIC_ENABLED,
        CLIENT_TOTAL_GB,
        CLIENT_USED_BYTES,
        CLIENT_REMAINING_BYTES,
        CLIENT_INBOUNDS_COUNT,
        EXPIRE_TIME,
        LAST_ONLINE,
        CLIENT_EXPIRED,
    ]

    for email in stale_emails:
        for metric in client_metrics:
            safe_remove(metric, email)

    purge_counter_cache_by_prefix_and_keypos('client_', 1, current_emails)
    _known_client_emails = set(current_emails)


def cleanup_inbound_series(current_inbounds):
    global _known_inbounds

    stale_inbounds = _known_inbounds - current_inbounds
    if not stale_inbounds:
        _known_inbounds = set(current_inbounds)
        return

    log.info("Cleaning up stale inbound series: %d", len(stale_inbounds))

    for remark, protocol in stale_inbounds:
        safe_remove(INBOUND_UP, remark, protocol)
        safe_remove(INBOUND_DOWN, remark, protocol)
        safe_remove(INBOUND_ENABLE, remark, protocol)

    valid_first_labels = set(x[0] for x in current_inbounds)
    stale_keys = []
    for key in _prev_counter_values.keys():
        if not isinstance(key, tuple) or not key:
            continue
        if key[0] not in ('inbound_up', 'inbound_down'):
            continue
        # key = ('inbound_up', remark, protocol)
        if len(key) < 3:
            continue
        if (key[1], key[2]) not in current_inbounds:
            stale_keys.append(key)

    for key in stale_keys:
        _prev_counter_values.pop(key, None)

    _known_inbounds = set(current_inbounds)


def cleanup_xray_version_series(current_versions):
    global _known_xray_versions

    stale_versions = _known_xray_versions - current_versions
    if not stale_versions:
        _known_xray_versions = set(current_versions)
        return

    for version in stale_versions:
        safe_remove(XRAY_VERSION, version)

    _known_xray_versions = set(current_versions)


# ============================================================
# Collectors
# ============================================================

def collect_client_metrics(clients, last_online_map, clients_summary=None):
    current_time = time.time()
    clients_seen = set()
    active_count = 0
    online_count = 0

    for client in clients or []:
        email = client.get('email')
        if not email or email in clients_seen:
            continue

        clients_seen.add(email)

        enable = bool(client.get('enable', False))
        inbound_ids = client.get('inboundIds') or []

        try:
            total_gb = int(client.get('totalGB', 0) or 0)
        except (TypeError, ValueError):
            total_gb = 0

        try:
            expiry = int(client.get('expiryTime', 0) or 0)
        except (TypeError, ValueError):
            expiry = 0

        traffic = client.get('traffic') or {}
        try:
            up = int(traffic.get('up', 0) or 0)
        except (TypeError, ValueError):
            up = 0

        try:
            down = int(traffic.get('down', 0) or 0)
        except (TypeError, ValueError):
            down = 0

        traffic_enable = bool(traffic.get('enable', True))

        used = up + down
        if total_gb > 0:
            remaining = max(total_gb - used, 0)
        else:
            remaining = 0

        last_online_ms = last_online_map.get(email, 0)
        last_online_sec = last_online_ms / 1000 if last_online_ms > 0 else 0

        if last_online_sec > 0 and (current_time - last_online_sec) <= ONLINE_THRESHOLD_SECONDS:
            is_online = 1
            online_count += 1
        else:
            is_online = 0

        expired = 1 if (expiry > 0 and (expiry / 1000) < current_time) else 0

        if enable:
            active_count += 1

        update_remote_counter(UP_BYTES.labels(email=email), ('client_up', email), up)
        update_remote_counter(DOWN_BYTES.labels(email=email), ('client_down', email), down)
        update_remote_counter(TRAFFIC_TOTAL.labels(email=email), ('client_total', email), used)

        ONLINE.labels(email=email).set(is_online)
        CLIENT_ENABLED.labels(email=email).set(1 if enable else 0)
        CLIENT_TRAFFIC_ENABLED.labels(email=email).set(1 if traffic_enable else 0)
        CLIENT_TOTAL_GB.labels(email=email).set(total_gb)
        CLIENT_USED_BYTES.labels(email=email).set(used)
        CLIENT_REMAINING_BYTES.labels(email=email).set(remaining)
        CLIENT_INBOUNDS_COUNT.labels(email=email).set(len(inbound_ids))
        EXPIRE_TIME.labels(email=email).set(expiry / 1000 if expiry > 0 else 0)
        LAST_ONLINE.labels(email=email).set(last_online_sec)
        CLIENT_EXPIRED.labels(email=email).set(expired)

    # Если paged summary есть — total/active можно взять оттуда.
    # Online всё равно оставляем по lastOnline threshold, чтобы была единая логика.
    if clients_summary:
        try:
            TOTAL_CLIENTS.set(int(clients_summary.get('total', len(clients_seen)) or len(clients_seen)))
        except Exception:
            TOTAL_CLIENTS.set(len(clients_seen))

        try:
            ACTIVE_CLIENTS.set(int(clients_summary.get('active', active_count) or active_count))
        except Exception:
            ACTIVE_CLIENTS.set(active_count)
    else:
        TOTAL_CLIENTS.set(len(clients_seen))
        ACTIVE_CLIENTS.set(active_count)

    ONLINE_CLIENTS.set(online_count)

    cleanup_client_series(clients_seen)

    log.info(
        "[clients] total_seen=%d active=%d online=%d",
        len(clients_seen),
        active_count,
        online_count
    )


def collect_inbound_metrics(inbounds):
    current_inbounds = set()

    for inbound in inbounds or []:
        remark = str(inbound.get('remark') or 'unknown')
        protocol = str(inbound.get('protocol') or 'unknown')

        current_inbounds.add((remark, protocol))

        try:
            up = int(inbound.get('up', 0) or 0)
        except (TypeError, ValueError):
            up = 0

        try:
            down = int(inbound.get('down', 0) or 0)
        except (TypeError, ValueError):
            down = 0

        enable = bool(inbound.get('enable', False))

        update_remote_counter(
            INBOUND_UP.labels(remark=remark, protocol=protocol),
            ('inbound_up', remark, protocol),
            up
        )
        update_remote_counter(
            INBOUND_DOWN.labels(remark=remark, protocol=protocol),
            ('inbound_down', remark, protocol),
            down
        )

        INBOUND_ENABLE.labels(remark=remark, protocol=protocol).set(1 if enable else 0)

    cleanup_inbound_series(current_inbounds)

    log.info("[inbounds] total=%d", len(current_inbounds))


def collect_system_metrics(status):
    CPU_USAGE.set(float(status.get('cpu', 0) or 0))

    mem = status.get('mem') or {}
    MEMORY_USED.set(int(mem.get('current', 0) or 0))
    MEMORY_TOTAL.set(int(mem.get('total', 0) or 0))

    disk = status.get('disk') or {}
    DISK_USED.set(int(disk.get('current', 0) or 0))
    DISK_TOTAL.set(int(disk.get('total', 0) or 0))

    net_io = status.get('netIO') or {}
    update_remote_counter(NET_UP, ('system_net_up',), int(net_io.get('up', 0) or 0))
    update_remote_counter(NET_DOWN, ('system_net_down',), int(net_io.get('down', 0) or 0))

    xray = status.get('xray') or {}
    XRAY_STATE.set(1 if xray.get('state') == 'running' else 0)

    version = str(xray.get('version') or 'unknown')
    XRAY_VERSION.labels(version=version).set(1)
    cleanup_xray_version_series({version})


# ============================================================
# Main loop
# ============================================================

def run_once():
    cycle_start = time.monotonic()
    full_success = True

    clients = None
    clients_summary = None
    inbounds = None
    status = None

    # 1. Клиенты
    try:
        clients, clients_summary = fetch_clients()
    except Exception as e:
        full_success = False
        EXPORTER_ERRORS.labels(stage='clients').inc()
        log.exception("Error collecting clients: %s", e)

    # 2. Инбаунды
    try:
        inbounds = fetch_inbounds()
    except Exception as e:
        full_success = False
        EXPORTER_ERRORS.labels(stage='inbounds').inc()
        log.exception("Error collecting inbounds: %s", e)

    # 3. Клиентские метрики обновляем только если клиенты успешно получены
    if clients is not None:
        try:
            last_online_map = build_last_online_map(inbounds or [])
            collect_client_metrics(clients, last_online_map, clients_summary=clients_summary)
        except Exception as e:
            full_success = False
            EXPORTER_ERRORS.labels(stage='client_metrics').inc()
            log.exception("Error processing client metrics: %s", e)

    # 4. Inbound метрики
    if inbounds is not None:
        try:
            collect_inbound_metrics(inbounds)
        except Exception as e:
            full_success = False
            EXPORTER_ERRORS.labels(stage='inbound_metrics').inc()
            log.exception("Error processing inbound metrics: %s", e)

    # 5. Системные метрики
    try:
        status = fetch_status()
        collect_system_metrics(status)
    except Exception as e:
        full_success = False
        EXPORTER_ERRORS.labels(stage='system').inc()
        log.exception("Error collecting system metrics: %s", e)

    # 6. Xray metrics state — опционально
    try:
        touch_xray_metrics_state()
    except Exception as e:
        EXPORTER_ERRORS.labels(stage='xray_metrics_state').inc()
        log.debug("xrayMetricsState unavailable: %s", e)

    duration = time.monotonic() - cycle_start
    EXPORTER_SCRAPE_DURATION.set(duration)
    EXPORTER_LAST_SCRAPE_TS.set(time.time())
    EXPORTER_UP.set(1 if full_success else 0)


def main():
    if not XUI_TOKEN:
        raise RuntimeError('Environment variable XUI_API_TOKEN is not set')

    start_http_server(LISTEN_PORT)

    log.info("=" * 60)
    log.info("3X-UI Prometheus Exporter v4.0")
    log.info("Panel URL: %s", XUI_URL)
    log.info("Listen port: %s", LISTEN_PORT)
    log.info("Scrape interval: %s sec", SCRAPE_INTERVAL)
    log.info("Request timeout: %s sec", REQUEST_TIMEOUT)
    log.info("Online threshold: %s sec", ONLINE_THRESHOLD_SECONDS)
    log.info("Use paged clients API: %s", USE_PAGED_CLIENTS)
    log.info("Clients page size: %s", CLIENTS_PAGE_SIZE)
    log.info("Metrics available on :%s/metrics", LISTEN_PORT)
    log.info("=" * 60)

    while True:
        try:
            run_once()
        except Exception as e:
            EXPORTER_UP.set(0)
            EXPORTER_ERRORS.labels(stage='cycle').inc()
            log.exception("Unexpected cycle error: %s", e)

        time.sleep(SCRAPE_INTERVAL)


if __name__ == '__main__':
    main()

