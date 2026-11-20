# -*- coding: utf-8 -*-
"""
legal.py — Disclaimer versi percobaan + logika batas waktu (trial).

Aplikasi ini adalah VERSI PERCOBAAN yang berlaku sampai EXPIRY_DATE.
Setelah tanggal itu aplikasi berhenti berfungsi (hanya menampilkan
pesan berakhir). Mekanisme anti-mundur jam sederhana: tanggal terakhir
yang pernah dilihat aplikasi disimpan; tanggal efektif adalah yang
terbaru di antara jam HP dan catatan itu.
"""

import datetime
import json
import os

# Masa percobaan: 2 bulan sejak 20 November 2026.
EXPIRY_DATE = datetime.date(2027, 1, 20)
EXPIRY_TEXT = '20 Januari 2027'

DISCLAIMER_VERSION = 1

DISCLAIMER_TEXT = """DISCLAIMER — VERSI PERCOBAAN

1. APLIKASI INDEPENDEN
Aplikasi ini adalah alat bantu independen untuk teknisi dan pengguna. Aplikasi ini BUKAN produk resmi Hikvision atau EZVIZ, dan tidak berafiliasi, tidak disponsori, serta tidak didukung oleh Hikvision Digital Technology Co., Ltd. maupun EZVIZ Inc. Nama "Hikvision" dan "EZVIZ" hanya disebut untuk menunjukkan jenis perangkat yang kompatibel.

2. HANYA UNTUK PERANGKAT YANG ANDA MILIKI / KELOLA DENGAN IZIN
Gunakan aplikasi ini hanya pada perangkat yang Anda miliki sendiri, atau yang pemiliknya telah memberi Anda izin untuk mengelolanya. Mengubah pengaturan perangkat milik orang lain tanpa izin dapat melanggar hukum (antara lain UU Informasi dan Transaksi Elektronik) dan menjadi tanggung jawab penuh pengguna.

3. MEMERLUKAN KATA SANDI PERANGKAT
Setiap perubahan IP/DHCP memerlukan kata sandi admin perangkat yang sah (untuk perangkat EZVIZ: kata sandi perangkat / Verification Code pada stiker perangkat). Aplikasi ini TIDAK membobol, menebak, melewati, atau memulihkan kata sandi dengan cara apa pun.

4. TIDAK ADA PENGUMPULAN DATA
Aplikasi ini tidak mengumpulkan, menyimpan, atau mengirim kata sandi maupun data Anda ke server mana pun. Seluruh proses berjalan secara lokal antara HP Anda dan perangkat di jaringan yang sama.

5. RISIKO PERUBAHAN JARINGAN
Mengubah IP/DHCP dapat membuat perangkat untuk sementara tidak terjangkau (misalnya bila IP baru berada di segmen jaringan yang berbeda). Pastikan Anda memahami akibatnya sebelum menerapkan perubahan. Pengembang tidak bertanggung jawab atas gangguan layanan, kehilangan akses, kesalahan konfigurasi, atau kerugian lain yang timbul dari penggunaan aplikasi ini.

6. KOMPATIBILITAS TIDAK DIJAMIN
Tidak semua model dan firmware perangkat menerima perintah perubahan jaringan melalui protokol ini. Sebagian perangkat mungkin menolak perintah (balasan "denied"/"failure") dan hanya dapat diatur melalui perangkat lunak PC resmi dari produsen.

7. VERSI PERCOBAAN BERBATAS WAKTU
Aplikasi ini adalah versi percobaan yang berlaku sampai 20 Januari 2027. Setelah tanggal tersebut aplikasi berhenti berfungsi. Aplikasi disediakan "sebagaimana adanya" (as is), tanpa jaminan dalam bentuk apa pun, termasuk jaminan kesesuaian untuk tujuan tertentu.

Dengan melanjutkan, Anda menyatakan telah membaca, memahami, dan menyetujui seluruh isi disclaimer ini."""

STATE_FILENAME = 'ipsetter_state.json'


def _state_path(base_dir):
    return os.path.join(base_dir, STATE_FILENAME)


def load_state(base_dir):
    """Baca state lokal (persetujuan disclaimer + tanggal terakhir terlihat)."""
    try:
        with open(_state_path(base_dir), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_state(base_dir, state):
    try:
        os.makedirs(base_dir, exist_ok=True)
        with open(_state_path(base_dir), 'w', encoding='utf-8') as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _parse_date(text):
    try:
        return datetime.date.fromisoformat(text)
    except Exception:
        return None


def effective_today(base_dir, today=None):
    """
    Tanggal efektif = maks(jam HP hari ini, tanggal terakhir yang pernah
    dilihat aplikasi). Catatan diperbarui setiap pemanggilan, supaya
    memundurkan jam HP tidak memperpanjang masa percobaan.
    """
    today = today or datetime.date.today()
    state = load_state(base_dir)
    last_seen = _parse_date(state.get('last_seen_date', ''))
    effective = max(today, last_seen) if last_seen else today
    if last_seen != effective:
        state['last_seen_date'] = effective.isoformat()
        save_state(base_dir, state)
    return effective


def trial_status(base_dir, today=None):
    """{'expired': bool, 'days_left': int, 'effective': date}"""
    effective = effective_today(base_dir, today=today)
    days_left = (EXPIRY_DATE - effective).days
    return {
        'expired': effective > EXPIRY_DATE,
        'days_left': days_left,
        'effective': effective,
    }


def disclaimer_accepted(state):
    return state.get('disclaimer_version') == DISCLAIMER_VERSION and \
        bool(state.get('disclaimer_accepted'))


def accept_disclaimer(base_dir, state=None):
    state = state if state is not None else load_state(base_dir)
    state['disclaimer_accepted'] = True
    state['disclaimer_version'] = DISCLAIMER_VERSION
    save_state(base_dir, state)
    return state
