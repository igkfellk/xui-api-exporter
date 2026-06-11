#!/usr/bin/env python3
import os
import requests
import time
from prometheus_client import start_http_server, Counter, Gauge

# Конфигурация
XUI_URL = os.getenv('XUI_URL', 'http://xui:2053')
XUI_TOKEN = os.getenv('XUI_API_TOKEN')

# Метрики клиентов
UP_BYTES = Counter('xui_client_up_bytes_total', 'Upload bytes per client', ['email'])
DOWN_BYTES = Counter('xui_client_down_bytes_total', 'Download bytes per client', ['email'])
ONLINE = Gauge('xui_client_online', 'Client online status (1=online, 0=offline)', ['email'])
TRAFFIC_TOTAL = Counter('xui_client_traffic_total_bytes', 'Total traffic per client', ['email'])
EXPIRE_TIME = Gauge('xui_client_expire_timestamp', 'Client expiration timestamp', ['email'])
LAST_ONLINE = Gauge('xui_client_last_online_timestamp', 'Last online timestamp in seconds', ['email'])

# Метрики инбаундов
INBOUND_UP = Counter('xui_inbound_up_bytes_total', 'Upload bytes per inbound', ['remark', 'protocol'])
INBOUND_DOWN = Counter('xui_inbound_down_bytes_total', 'Download bytes per inbound', ['remark', 'protocol'])
INBOUND_ENABLE = Gauge('xui_inbound_enable', 'Inbound enable status', ['remark', 'protocol'])

# Системные метрики
CPU_USAGE = Gauge('xui_system_cpu_usage_percent', 'CPU usage percentage')
MEMORY_USED = Gauge('xui_system_memory_used_bytes', 'Memory used in bytes')
MEMORY_TOTAL = Gauge('xui_system_memory_total_bytes', 'Memory total in bytes')
DISK_USED = Gauge('xui_system_disk_used_bytes', 'Disk used in bytes')
DISK_TOTAL = Gauge('xui_system_disk_total_bytes', 'Disk total in bytes')
NET_UP = Counter('xui_system_network_up_bytes_total', 'Network upload bytes')
NET_DOWN = Counter('xui_system_network_down_bytes_total', 'Network download bytes')
XRAY_STATE = Gauge('xui_xray_state', 'Xray state (1=running, 0=stopped)')
XRAY_VERSION = Gauge('xui_xray_version_info', 'Xray version info', ['version'])

# Метрики подписок
TOTAL_CLIENTS = Gauge('xui_total_clients', 'Total number of clients')
ACTIVE_CLIENTS = Gauge('xui_active_clients', 'Number of active clients')
ONLINE_CLIENTS = Gauge('xui_online_clients', 'Number of online clients')

# Порог онлайна в секундах (1 минута = 60 секунд)
ONLINE_THRESHOLD_SECONDS = 60

def collect_client_metrics():
    """Сбор метрик клиентов из /panel/api/inbounds/list с использованием lastOnline"""
    headers = {'Authorization': f'Bearer {XUI_TOKEN}', 'Accept': 'application/json'}
    
    try:
        resp = requests.get(f'{XUI_URL}/panel/api/inbounds/list', headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('success'):
            online_count = 0
            active_count = 0
            clients_seen = set()
            current_time = time.time()  # текущее время в секундах
            
            for inbound in data.get('obj', []):
                client_stats = inbound.get('clientStats', [])
                for client in client_stats:
                    email = client.get('email')
                    if not email or email in clients_seen:
                        continue
                    clients_seen.add(email)
                    
                    up = client.get('up', 0)
                    down = client.get('down', 0)
                    enable = client.get('enable', False)
                    expiry = client.get('expiryTime', 0)
                    last_online_ms = client.get('lastOnline', 0)
                    
                    # Преобразуем lastOnline из миллисекунд в секунды
                    last_online_sec = last_online_ms / 1000 if last_online_ms > 0 else 0
                    
                    # Определяем онлайн статус
                    if last_online_sec > 0 and (current_time - last_online_sec) <= ONLINE_THRESHOLD_SECONDS:
                        is_online = 1
                        online_count += 1
                    else:
                        is_online = 0
                    
                    if enable:
                        active_count += 1
                    
                    # Обновляем метрики
                    UP_BYTES.labels(email=email).inc(up)
                    DOWN_BYTES.labels(email=email).inc(down)
                    TRAFFIC_TOTAL.labels(email=email).inc(up + down)
                    EXPIRE_TIME.labels(email=email).set(expiry / 1000)
                    LAST_ONLINE.labels(email=email).set(last_online_sec)
                    ONLINE.labels(email=email).set(is_online)
            
            TOTAL_CLIENTS.set(len(clients_seen))
            ACTIVE_CLIENTS.set(active_count)
            ONLINE_CLIENTS.set(online_count)
            
            print(f"Collected: {len(clients_seen)} clients, {online_count} online, {active_count} active")
            
    except Exception as e:
        print(f"Error collecting client metrics: {e}")

def collect_inbound_metrics():
    """Сбор метрик инбаундов из /panel/api/inbounds/list"""
    headers = {'Authorization': f'Bearer {XUI_TOKEN}', 'Accept': 'application/json'}
    
    try:
        resp = requests.get(f'{XUI_URL}/panel/api/inbounds/list', headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('success'):
            for inbound in data.get('obj', []):
                remark = inbound.get('remark', 'unknown')
                protocol = inbound.get('protocol', 'unknown')
                up = inbound.get('up', 0)
                down = inbound.get('down', 0)
                enable = inbound.get('enable', False)
                
                INBOUND_UP.labels(remark=remark, protocol=protocol).inc(up)
                INBOUND_DOWN.labels(remark=remark, protocol=protocol).inc(down)
                INBOUND_ENABLE.labels(remark=remark, protocol=protocol).set(1 if enable else 0)
                
    except Exception as e:
        print(f"Error collecting inbound metrics: {e}")

def collect_system_metrics():
    """Сбор системных метрик из /panel/api/server/status"""
    headers = {'Authorization': f'Bearer {XUI_TOKEN}', 'Accept': 'application/json'}
    
    try:
        resp = requests.get(f'{XUI_URL}/panel/api/server/status', headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('success'):
            status = data.get('obj', {})
            
            CPU_USAGE.set(status.get('cpu', 0))
            
            mem = status.get('mem', {})
            MEMORY_USED.set(mem.get('current', 0))
            MEMORY_TOTAL.set(mem.get('total', 1))
            
            disk = status.get('disk', {})
            DISK_USED.set(disk.get('current', 0))
            DISK_TOTAL.set(disk.get('total', 1))
            
            net_io = status.get('netIO', {})
            NET_UP.inc(net_io.get('up', 0))
            NET_DOWN.inc(net_io.get('down', 0))
            
            xray = status.get('xray', {})
            xray_state = 1 if xray.get('state') == 'running' else 0
            XRAY_STATE.set(xray_state)
            
            xray_version = xray.get('version', 'unknown')
            XRAY_VERSION.labels(version=xray_version).set(1)
            
    except Exception as e:
        print(f"Error collecting system metrics: {e}")

def collect_xray_metrics():
    """Сбор метрик Xray из /panel/api/server/xrayMetricsState"""
    headers = {'Authorization': f'Bearer {XUI_TOKEN}', 'Accept': 'application/json'}
    
    try:
        resp = requests.get(f'{XUI_URL}/panel/api/server/xrayMetricsState', headers=headers, timeout=10)
        data = resp.json()
        
        if data.get('success') and data.get('obj'):
            metrics = data.get('obj', {})
            # Можно добавить метрики Xray
            
    except Exception as e:
        # Xray metrics могут быть не включены - игнорируем
        pass

if __name__ == '__main__':
    start_http_server(9090)
    print("=" * 60)
    print("3X-UI Prometheus Exporter v2.0")
    print(f"Panel URL: {XUI_URL}")
    print(f"Online threshold: {ONLINE_THRESHOLD_SECONDS} seconds ({(ONLINE_THRESHOLD_SECONDS/60):.0f} minutes)")
    print("Metrics available on :9090/metrics")
    print("=" * 60)
    
    while True:
        collect_client_metrics()
        collect_inbound_metrics()
        collect_system_metrics()
        collect_xray_metrics()
        time.sleep(30)
