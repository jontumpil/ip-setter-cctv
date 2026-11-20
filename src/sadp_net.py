# -*- coding: utf-8 -*-
"""
sadp_net.py — Mesin SADP untuk IP Setter CCTV (Trial).

Dua fungsi:
1. Discovery perangkat Hikvision / EZVIZ lewat protokol SADP
   (UDP 37020; multicast 239.255.255.250 + broadcast per-interface).
2. Mengubah parameter jaringan perangkat (DHCP on/off, IPv4, subnet
   mask, gateway, command port) lewat pesan SADP <Types>update</Types>.

Pola transport meniru yang sudah terbukti di lapangan pada CCTV
Scanner v1.9.1+: SATU socket listener INADDR_ANY (kunci penerimaan
balasan broadcast/multicast dari perangkat beda segmen) + socket kirim
per-interface + Android MulticastLock. Probe/perintah dikirim ulang
berkala selama jendela dengar karena satu paket broadcast mudah hilang.

Format pesan 'update' mengikuti implementasi terbuka hikvision-tooling
(github.com/cameronnewman/hikvision-tooling, internal/sadp/commands.go)
dan cocok dengan amplop tangkapan paket SADP Tool resmi
(Uuid / Types=update / PWErrorParse / MAC / IPv4Address / CommandPort).
Kata sandi dikirim sebagai teks biasa di dalam <Password>, sama seperti
implementasi rujukan. Catatan: SADP Tool resmi versi baru mengirim kata
sandi dalam bentuk terenkripsi (atribut bSalt) — sebagian firmware
terbaru KEMUNGKINAN menolak bentuk teks biasa; itu hanya bisa dipastikan
lewat uji perangkat nyata, dan balasan perangkat ditampilkan apa adanya.
"""

import ipaddress
import logging
import re
import select
import socket
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager

log = logging.getLogger('ipsetter.sadp')

SADP_PORT = 37020
SADP_MULTICAST_ADDR = '239.255.255.250'
RESEND_INTERVAL = 1.5          # detik antar pengiriman ulang probe/perintah
DEFAULT_DISCOVERY_TIMEOUT = 6.0
DEFAULT_UPDATE_TIMEOUT = 8.0

LOCK_STATUS = {'multicast': False, 'wifi': False, 'detail': ''}


def _new_uuid():
    return str(uuid.uuid4()).upper()


def _xml_escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _norm_mac(mac):
    return re.sub(r'[^0-9a-fA-F]', '', mac or '').lower()


def _mac_in_text(raw_text, target_mac_norm):
    """True bila MAC target muncul di teks mentah dalam bentuk umum
    (pisah '-' / ':' / menyatu) — gaya pencocokan hikvision-tooling."""
    if not target_mac_norm or len(target_mac_norm) != 12:
        return False
    pairs = [target_mac_norm[i:i + 2] for i in range(0, 12, 2)]
    candidates = ('-'.join(pairs), ':'.join(pairs), target_mac_norm)
    lowered = (raw_text or '').lower()
    return any(c in lowered for c in candidates)


def valid_ipv4(text):
    try:
        ipaddress.IPv4Address((text or '').strip())
        return True
    except Exception:
        return False


# ── Daftar interface lokal ────────────────────────────────────────────────────

def _get_local_interfaces():
    """
    Daftar semua interface IPv4 aktif: [{'name','ip','broadcast'}].

    Socket yang di-bind ke INADDR_ANY dan mengirim ke 255.255.255.255
    hanya keluar lewat SATU interface pilihan OS (default route).
    SADP resmi mengirim broadcast dari SETIAP interface — selisih ini
    yang membuat device di segmen lain tidak terlihat.

    Strategi (stdlib saja): ioctl SIOCGIFCONF + SIOCGIFBRDADDR di
    Linux/Android, fallback hostname + IP utama.
    """
    ifaces = []
    seen_ips = set()

    def _add(name, ip, broadcast):
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            return
        if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
            return
        if ip in seen_ips:
            return
        seen_ips.add(ip)
        if not broadcast:
            broadcast = str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address)
        ifaces.append({'name': name or ip, 'ip': ip, 'broadcast': broadcast})

    try:
        import fcntl
        import array
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        buf = array.array('B', b'\0' * 4096)
        out = fcntl.ioctl(s.fileno(), 0x8912,
                          struct.pack('iL', buf.buffer_info()[1], buf.buffer_info()[0]))
        length = struct.unpack('iL', out)[0]
        data = buf.tobytes()[:length]
        names = []
        for i in range(0, len(data), 40):  # struct ifreq = 40 byte di Linux 64-bit
            name = data[i:i + 16].split(b'\0')[0].decode(errors='ignore')
            fam = struct.unpack_from('<H', data, i + 16)[0]
            if fam == socket.AF_INET and name:
                ip = socket.inet_ntoa(data[i + 20:i + 24])
                names.append((name, ip))
        for name, ip in names:
            brd = None
            try:
                ifreq = struct.pack('256s', name.encode()[:15])
                res = fcntl.ioctl(s.fileno(), 0x8919, ifreq)  # SIOCGIFBRDADDR
                brd = socket.inet_ntoa(res[20:24])
                if brd == '0.0.0.0':
                    brd = None
            except Exception:
                brd = None
            _add(name, ip, brd)
        s.close()
    except Exception as e:
        log.debug(f'[iface] ioctl gagal: {e}')

    if not ifaces:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                _add('hostname', info[4][0], None)
        except Exception:
            pass
        try:
            tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tmp.connect(('8.8.8.8', 80))
            _add('primary', tmp.getsockname()[0], None)
            tmp.close()
        except Exception:
            pass

    log.debug(f'[iface] {len(ifaces)} interface: {ifaces}')
    return ifaces


# ── Android MulticastLock ─────────────────────────────────────────────────────

@contextmanager
def _android_multicast_lock():
    """
    Di Android, paket multicast DIBUANG di driver Wi-Fi kecuali aplikasi
    memegang WifiManager.MulticastLock (perlu permission
    CHANGE_WIFI_MULTICAST_STATE). Sekalian pegang WifiLock supaya radio
    Wi-Fi tidak ditidurkan selama operasi. Di luar APK Android
    (Termux/desktop) blok ini otomatis no-op.
    """
    mlock = None
    wlock = None
    LOCK_STATUS.update({'multicast': False, 'wifi': False, 'detail': ''})
    try:
        from jnius import autoclass
        activity = None
        for cls_name in ('org.kivy.android.PythonActivity',
                         'org.renpy.android.PythonSDLActivity'):
            try:
                activity = autoclass(cls_name).mActivity
                if activity is not None:
                    break
            except Exception:
                activity = None
        if activity is None:
            raise RuntimeError('activity Android tidak ditemukan')
        Context = autoclass('android.content.Context')
        WifiManager = autoclass('android.net.wifi.WifiManager')
        wifi = activity.getSystemService(Context.WIFI_SERVICE)
        mlock = wifi.createMulticastLock('ipsetter_discovery')
        mlock.setReferenceCounted(False)
        mlock.acquire()
        LOCK_STATUS['multicast'] = bool(mlock.isHeld())
        try:
            wlock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL,
                                        'ipsetter_wifi')
            wlock.setReferenceCounted(False)
            wlock.acquire()
            LOCK_STATUS['wifi'] = bool(wlock.isHeld())
        except Exception as e:
            LOCK_STATUS['detail'] += f' wifilock gagal: {e}'
    except Exception as e:
        LOCK_STATUS['detail'] = f'tidak tersedia: {e}'
        mlock = None
    try:
        yield
    finally:
        for lk in (mlock, wlock):
            if lk is not None:
                try:
                    lk.release()
                except Exception:
                    pass


# ── Pembukaan socket ──────────────────────────────────────────────────────────

def _open_interface_sockets(port, interfaces, multicast_groups):
    """
    - SATU socket listener INADDR_ANY (bind 0.0.0.0:port) — KUNCI
      PENERIMAAN. Socket yang di-bind ke IP unicast sebuah interface
      HANYA menerima paket unicast ke IP itu; balasan discovery yang
      dialamatkan ke broadcast / grup multicast (cara device beda
      segmen membalas) TIDAK diteruskan kernel ke socket tersebut.
      Listener 0.0.0.0 menerima semuanya.
    - Satu socket per interface (bind ke IP interface) untuk MENGIRIM
      dengan alamat sumber & interface keluar yang benar, plus join
      grup multicast dari interface itu.
    Return: list of (sock, iface_dict) — listener ditandai iface['any'].
    """
    out = []
    ifaces = list(interfaces or [])

    def _mk(bind_ip):
        sk = None
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sk.bind((bind_ip, port))
            return sk
        except OSError:
            if sk:
                sk.close()
            return None

    def _join(sk, group, iface_ip):
        try:
            mreq = struct.pack('4s4s', socket.inet_aton(group),
                               socket.inet_aton(iface_ip or '0.0.0.0'))
            sk.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            return True
        except OSError as e:
            log.debug(f'[sadp] join {group} via {iface_ip} gagal: {e}')
            return False

    # a) Listener INADDR_ANY — join grup dari SEMUA interface
    lsock = _mk('')
    if lsock is not None:
        try:
            lsock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
        except OSError:
            pass
        for group in multicast_groups or []:
            _join(lsock, group, '')
            for iface in ifaces:
                if iface.get('ip'):
                    _join(lsock, group, iface['ip'])
        lsock.setblocking(False)
        out.append((lsock, {'name': 'any', 'ip': '',
                            'broadcast': '255.255.255.255', 'any': True}))

    # b) Socket per interface untuk pengiriman
    for iface in ifaces:
        ip = iface.get('ip', '')
        if not ip:
            continue
        sock = _mk(ip)
        if sock is None:
            sock = _mk('')
            if sock is None:
                continue
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(ip))
            for group in multicast_groups or []:
                _join(sock, group, ip)
        except OSError:
            pass
        sock.setblocking(False)
        out.append((sock, iface))
    return out


def _send_from_all(sockets, interfaces, payload, extra_unicast_ip=None):
    """Kirim payload ke broadcast umum + grup multicast + directed
    broadcast tiap interface (+ unicast opsional ke satu IP)."""
    all_broadcasts = {i['broadcast'] for i in interfaces if i.get('broadcast')}
    for sock, iface in sockets:
        dests = [('255.255.255.255', SADP_PORT),
                 (SADP_MULTICAST_ADDR, SADP_PORT)]
        if iface.get('any'):
            for brd in all_broadcasts:
                if brd != '255.255.255.255':
                    dests.append((brd, SADP_PORT))
        else:
            brd = iface.get('broadcast')
            if brd and brd != '255.255.255.255':
                dests.append((brd, SADP_PORT))
        if extra_unicast_ip:
            dests.append((extra_unicast_ip, SADP_PORT))
        for dest in dests:
            try:
                sock.sendto(payload, dest)
            except OSError as e:
                log.debug(f'[sadp] sendto {dest} gagal: {e}')


# ── Parsing respons ───────────────────────────────────────────────────────────

def parse_sadp_fields(data):
    """Parse XML respons SADP menjadi dict {tag huruf kecil: text}."""
    try:
        text = data.decode('utf-8', errors='ignore')
    except Exception:
        return {}
    fields = {}
    try:
        root = ET.fromstring(text)
        for child in root:
            tag = child.tag.split('}')[-1].lower()
            fields[tag] = (child.text or '').strip()
    except ET.ParseError:
        for m in re.finditer(r'<(\w+)>([^<]*)</\1>', text):
            fields[m.group(1).lower()] = m.group(2).strip()
    return fields


def _parse_dhcp(raw):
    val = (raw or '').strip().lower()
    if val in ('true', '1'):
        return True
    if val in ('false', '0'):
        return False
    return None


def parse_discovery_reply(data, src_ip):
    """
    Parse balasan inquiry (ProbeMatch) menjadi dict perangkat.
    Return None untuk paket yang bukan balasan discovery SADP
    (termasuk pantulan probe kita sendiri).
    """
    try:
        text = data.decode('utf-8', errors='ignore')
    except Exception:
        return None
    if '<Probe>' in text and '<ProbeMatch>' not in text:
        return None  # pantulan probe inquiry kita sendiri
    fields = parse_sadp_fields(data)
    if not fields:
        return None
    mac = fields.get('macaddress') or fields.get('mac') or ''
    if not mac and 'ipv4address' not in fields:
        return None
    return {
        'ip': fields.get('ipv4address') or fields.get('ip') or src_ip,
        'mac': mac,
        'model': fields.get('devicedescription') or 'Perangkat Hikvision/EZVIZ',
        'serial': fields.get('devicesn') or fields.get('serialnumber') or '',
        'firmware': fields.get('softwareversion') or '',
        'dhcp': _parse_dhcp(fields.get('dhcp')),
        'mask': fields.get('ipv4subnetmask') or '',
        'gateway': fields.get('ipv4gateway') or '',
        'command_port': fields.get('commandport') or '8000',
        'http_port': fields.get('httpport') or '',
        'activated': fields.get('activated') or '',
        'device_type': fields.get('devicetype') or '',
    }


# ── Discovery ─────────────────────────────────────────────────────────────────

def build_inquiry_probe(uuid_str=None):
    uuid_str = uuid_str or _new_uuid()
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<Probe><Uuid>{uuid_str}</Uuid>'
            '<Types>inquiry</Types></Probe>').encode('utf-8')


def discover(timeout=DEFAULT_DISCOVERY_TIMEOUT, stop_event=None,
             on_device=None, socket_factory=None, interfaces=None):
    """
    Jalankan discovery SADP selama `timeout` detik.
    on_device(device_dict) dipanggil untuk setiap perangkat BARU.
    Return (list_perangkat, info_dict) — info berisi status lock dan
    jumlah paket diterima, untuk diagnostik tampilan.
    socket_factory/interfaces dapat disuntikkan untuk pengujian.
    """
    if interfaces is None:
        interfaces = _get_local_interfaces()
    open_sockets = socket_factory or _open_interface_sockets
    probe = build_inquiry_probe()
    devices = {}
    info = {'rx': 0, 'interfaces': len(interfaces)}
    deadline = time.time() + timeout

    with _android_multicast_lock():
        sockets = open_sockets(SADP_PORT, interfaces, [SADP_MULTICAST_ADDR])
        if not sockets:
            info['error'] = 'Tidak ada socket UDP yang bisa dibuka.'
            return [], info
        try:
            next_send = 0.0
            while time.time() < deadline:
                if stop_event is not None and stop_event.is_set():
                    break
                now = time.time()
                if now >= next_send:
                    _send_from_all(sockets, interfaces, probe)
                    next_send = now + RESEND_INTERVAL
                try:
                    readable, _, _ = select.select(
                        [s for s, _ in sockets], [], [], 0.3)
                except (OSError, ValueError):
                    break
                for sock in readable:
                    try:
                        data, addr = sock.recvfrom(65535)
                    except OSError:
                        continue
                    info['rx'] += 1
                    dev = parse_discovery_reply(data, addr[0])
                    if not dev:
                        continue
                    key = _norm_mac(dev['mac']) or dev['ip']
                    if key in devices:
                        continue
                    devices[key] = dev
                    if on_device is not None:
                        try:
                            on_device(dev)
                        except Exception:
                            pass
        finally:
            for sock, _ in sockets:
                try:
                    sock.close()
                except Exception:
                    pass
    info['lock'] = dict(LOCK_STATUS)
    info['count'] = len(devices)
    return list(devices.values()), info


# ── Ubah parameter jaringan (SADP 'update') ───────────────────────────────────

def build_update_message(mac, password, ip, mask, gateway, dhcp,
                         command_port=8000, uuid_str=None):
    """
    Bangun pesan SADP <Types>update</Types> — format persis mengikuti
    implementasi rujukan hikvision-tooling:

    <Probe><Uuid/><Types>update</Types><PWErrorParse>true</PWErrorParse>
    <MAC/><Password/><IPv4Address/><CommandPort/><IPv4SubnetMask/>
    <IPv4Gateway/><DHCP>true|false</DHCP></Probe>
    """
    uuid_str = uuid_str or _new_uuid()
    try:
        command_port = int(command_port)
    except (TypeError, ValueError):
        command_port = 8000
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<Probe>',
        f'<Uuid>{uuid_str}</Uuid>',
        '<Types>update</Types>',
        '<PWErrorParse>true</PWErrorParse>',
        f'<MAC>{_xml_escape(mac or "")}</MAC>',
        f'<Password>{_xml_escape(password or "")}</Password>',
        f'<IPv4Address>{_xml_escape(ip or "")}</IPv4Address>',
        f'<CommandPort>{command_port}</CommandPort>',
        f'<IPv4SubnetMask>{_xml_escape(mask or "")}</IPv4SubnetMask>',
        f'<IPv4Gateway>{_xml_escape(gateway or "")}</IPv4Gateway>',
        f'<DHCP>{"true" if dhcp else "false"}</DHCP>',
        '</Probe>',
    ]
    return ''.join(parts).encode('utf-8')


def apply_network_settings(device, password, ip, mask, gateway, dhcp,
                           timeout=DEFAULT_UPDATE_TIMEOUT, stop_event=None,
                           socket_factory=None, interfaces=None):
    """
    Kirim pesan 'update' ke perangkat dan tunggu ActionResponse-nya.
    Return {'ok', 'result', 'fields', 'raw', 'error'}:
    - ok=True hanya bila perangkat membalas Result=success.
    - result: nilai Result mentah dari perangkat ('success' / 'failure'
      / 'denied' / '' bila tidak ada balasan).
    """
    target_mac = _norm_mac(device.get('mac', ''))
    target_ip = device.get('ip', '')
    if not target_mac:
        return {'ok': False, 'result': '', 'fields': {}, 'raw': '',
                'error': 'MAC perangkat tidak diketahui — perintah SADP butuh MAC.'}
    try:
        command_port = int(device.get('command_port') or 8000)
    except (TypeError, ValueError):
        command_port = 8000
    payload = build_update_message(
        device.get('mac', ''), password, ip, mask, gateway, dhcp,
        command_port=command_port)

    if interfaces is None:
        interfaces = _get_local_interfaces()
    open_sockets = socket_factory or _open_interface_sockets
    result = {'ok': False, 'result': '', 'fields': {}, 'raw': '', 'error': ''}
    deadline = time.time() + timeout

    with _android_multicast_lock():
        sockets = open_sockets(SADP_PORT, interfaces, [SADP_MULTICAST_ADDR])
        if not sockets:
            result['error'] = 'Tidak ada socket UDP yang bisa dibuka.'
            return result
        try:
            next_send = 0.0
            while time.time() < deadline:
                if stop_event is not None and stop_event.is_set():
                    result['error'] = 'Dibatalkan.'
                    return result
                now = time.time()
                if now >= next_send:
                    _send_from_all(sockets, interfaces, payload,
                                   extra_unicast_ip=target_ip or None)
                    next_send = now + RESEND_INTERVAL
                try:
                    readable, _, _ = select.select(
                        [s for s, _ in sockets], [], [], 0.3)
                except (OSError, ValueError):
                    break
                for sock in readable:
                    try:
                        data, addr = sock.recvfrom(65535)
                    except OSError:
                        continue
                    fields = parse_sadp_fields(data)
                    if not fields:
                        continue
                    if 'result' not in fields:
                        continue
                    # Pencocokan balasan: utamakan MAC (elemen <MAC> atau
                    # kemunculan MAC di teks mentah, gaya hikvision-
                    # tooling). Bila balasan sama sekali tidak memuat
                    # MAC, terima dari IP sumber perangkat target.
                    raw_text = data.decode('utf-8', errors='ignore')
                    mac_field = _norm_mac(fields.get('mac', ''))
                    mac_in_text = _mac_in_text(raw_text, target_mac)
                    if mac_field:
                        matched = mac_field == target_mac
                    elif mac_in_text:
                        matched = True
                    else:
                        matched = bool(target_ip) and addr[0] == target_ip
                    if matched:
                        res = fields.get('result', '').strip().lower()
                        result.update({
                            'ok': res == 'success',
                            'result': res,
                            'fields': fields,
                            'raw': data.decode('utf-8', errors='ignore'),
                        })
                        if not result['ok']:
                            result['error'] = (
                                f"Perangkat menolak perintah (Result: {res}).")
                        return result
            result['error'] = (
                f'Perangkat tidak menjawab dalam {timeout:.0f} detik. '
                'Pastikan HP dan perangkat berada di jaringan lokal yang '
                'sama (satu domain broadcast).')
            return result
        finally:
            for sock, _ in sockets:
                try:
                    sock.close()
                except Exception:
                    pass
