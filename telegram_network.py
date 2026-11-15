"""Per-socket physical routing; does not alter system routes or the user's VPN."""
from __future__ import annotations
import asyncio
import http.client
import ipaddress
import json
import os
import socket
import ssl
import struct
import subprocess
import time


def physical_interface():
    if os.name != 'nt':
        raise RuntimeError('Прямой выбор сетевого адаптера в этой бете доступен в Windows')
    command = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
               "Get-NetIPConfiguration | Where-Object { $_.NetAdapter.HardwareInterface "
               "-and $_.NetAdapter.Status -eq 'Up' -and $_.IPv4DefaultGateway } | "
               "Select-Object InterfaceAlias,InterfaceIndex,@{n='IPv4';e={$_.IPv4Address.IPAddress}} | "
               "ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Не удалось определить физический Wi-Fi/Ethernet адаптер')
    try:
        records = json.loads(result.stdout.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        raise RuntimeError('Не найден подключённый физический Wi-Fi/Ethernet адаптер') from None
    records = records if isinstance(records, list) else [records]
    records = [r for r in records if isinstance(r.get('IPv4'), str)]
    if len(records) != 1:
        raise RuntimeError('Для прямого выхода нужен один активный физический Wi-Fi/Ethernet адаптер')
    return dict(index=int(records[0]['InterfaceIndex']), source=records[0]['IPv4'])


class PhysicalRoute:
    def __init__(self, interface):
        self.interface = interface
        self.cache = {}
        self.original_open = asyncio.open_connection

    def socket(self):
        if self.interface is None:
            raise OSError('Физический маршрут временно недоступен')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', self.interface['index']))
            sock.bind((self.interface['source'], 0))
            if sock.getsockopt(socket.IPPROTO_IP, 31) != self.interface['index']:
                raise OSError('Индекс сетевого интерфейса не подтверждён')
            return sock
        except BaseException:
            sock.close()
            raise

    def resolve(self, host):
        try:
            address = ipaddress.ip_address(host)
            if address.version != 4:
                raise OSError('Прямой режим этой беты использует IPv4')
            return str(address)
        except ValueError:
            pass
        cached = self.cache.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        last = ''
        route = self

        class DNSConnection(http.client.HTTPSConnection):
            def connect(self):
                raw = route.socket()
                try:
                    raw.settimeout(5)
                    raw.connect((self.target_ip, 443))
                    self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                except BaseException:
                    raw.close()
                    raise

        from urllib.parse import quote
        for ip, server, template in (
            ('1.1.1.1', 'cloudflare-dns.com', '/dns-query?name={}&type=A'),
            ('8.8.8.8', 'dns.google', '/resolve?name={}&type=A'),
        ):
            conn = DNSConnection(server, timeout=5, context=ssl.create_default_context())
            conn.target_ip = ip
            try:
                conn.request('GET', template.format(quote(host, safe='')),
                             headers={'Accept': 'application/dns-json'})
                response = conn.getresponse()
                data = json.loads(response.read(32768))
                ips = [a['data'] for a in data.get('Answer', []) if a.get('type') == 1]
                if response.status == 200 and ips:
                    address = str(ipaddress.IPv4Address(ips[0]))
                    self.cache[host] = (time.monotonic() + 60, address)
                    return address
                last = 'DoH HTTP ' + str(response.status)
            except (OSError, ValueError, http.client.HTTPException) as exc:
                last = str(exc)
            finally:
                conn.close()
        raise OSError('Прямой DNS не ответил: ' + last)

    async def open_connection(self, host=None, port=None, **kwargs):
        # Asyncio's self-pipe and local control sockets are not outbound traffic.
        if host in (None, 'localhost', '127.0.0.1', '::1'):
            return await self.original_open(host, port, **kwargs)
        context = kwargs.get('ssl')
        if context and (not getattr(context, 'check_hostname', False) or
                        context.verify_mode != ssl.CERT_REQUIRED):
            raise OSError('Непроверяемое TLS-соединение отключено в этой бете')
        ip = await asyncio.to_thread(self.resolve, host)
        raw = self.socket()
        try:
            raw.setblocking(False)
            await asyncio.get_running_loop().sock_connect(raw, (ip, port))
            if raw.getsockname()[0] != self.interface['source']:
                raise OSError('Физический адрес соединения не подтверждён')
            return await self.original_open(sock=raw, **kwargs)
        except BaseException:
            raw.close()
            raise
