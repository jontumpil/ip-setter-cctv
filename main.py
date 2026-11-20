# -*- coding: utf-8 -*-
"""
IP Setter CCTV (Trial) — aplikasi Android untuk mendeteksi perangkat
Hikvision / EZVIZ di jaringan lokal (protokol SADP) dan mengubah
parameter jaringannya (DHCP / IP statis) langsung dari HP, tanpa PC.

VERSI PERCOBAAN: berlaku sampai 20 Januari 2027 (lihat src/legal.py).
Aplikasi independen — bukan produk resmi Hikvision/EZVIZ.
"""

import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from src import legal
from src import sadp_net


def _wrapped_label(text, **kwargs):
    """Label yang membungkus teks dan menyesuaikan tingginya sendiri."""
    lbl = Label(text=text, size_hint_y=None, halign='left', valign='top',
                **kwargs)
    lbl.bind(width=lambda s, w: s.setter('text_size')(s, (w, None)))
    lbl.bind(texture_size=lambda s, ts: s.setter('height')(s, ts[1]))
    return lbl


def _disclaimer_scroll():
    scroll = ScrollView(size_hint=(1, 1))
    scroll.add_widget(_wrapped_label(legal.DISCLAIMER_TEXT))
    return scroll


class IPSetterApp(App):
    title = 'IP Setter CCTV (Trial)'

    # ── Siklus hidup ─────────────────────────────────────────────────────
    def build(self):
        self.state_dir = self.user_data_dir
        self.state = legal.load_state(self.state_dir)
        self.trial = legal.trial_status(self.state_dir)
        self.scanning = False
        self.stop_event = None
        self.devices = {}
        self.root_box = BoxLayout(orientation='vertical')
        if self.trial['expired']:
            self._show_expired()
        elif not legal.disclaimer_accepted(self.state):
            self._show_disclaimer_gate()
        else:
            self._show_main()
        return self.root_box

    def _swap(self, layout):
        self.root_box.clear_widgets()
        self.root_box.add_widget(layout)

    # ── Layar masa percobaan berakhir ────────────────────────────────────
    def _show_expired(self):
        box = BoxLayout(orientation='vertical', padding=16, spacing=12)
        box.add_widget(_wrapped_label(
            '[b]Masa Percobaan Berakhir[/b]', markup=True))
        box.add_widget(_wrapped_label(
            'Versi percobaan aplikasi ini berlaku sampai '
            f'{legal.EXPIRY_TEXT} dan sekarang sudah berakhir, sehingga '
            'aplikasi tidak dapat digunakan lagi.\n\n'
            'Terima kasih sudah mencoba.'))
        btn = Button(text='Lihat Disclaimer', size_hint_y=None, height=48)
        btn.bind(on_release=lambda *_: self._show_disclaimer_popup())
        box.add_widget(btn)
        self._swap(box)

    # ── Gerbang disclaimer (pertama kali) ────────────────────────────────
    def _show_disclaimer_gate(self):
        box = BoxLayout(orientation='vertical', padding=12, spacing=10)
        box.add_widget(Label(
            text='IP Setter CCTV — Versi Percobaan',
            size_hint_y=None, height=32))
        box.add_widget(_disclaimer_scroll())
        btn = Button(text='Saya Mengerti dan Setuju',
                     size_hint_y=None, height=52)
        btn.bind(on_release=self._on_accept_disclaimer)
        box.add_widget(btn)
        self._swap(box)

    def _on_accept_disclaimer(self, *_):
        self.state = legal.accept_disclaimer(self.state_dir, self.state)
        self.trial = legal.trial_status(self.state_dir)
        if self.trial['expired']:
            self._show_expired()
        else:
            self._show_main()

    def _show_disclaimer_popup(self):
        box = BoxLayout(orientation='vertical', padding=8, spacing=8)
        box.add_widget(_disclaimer_scroll())
        btn = Button(text='Tutup', size_hint_y=None, height=48)
        box.add_widget(btn)
        popup = Popup(title='Disclaimer', content=box,
                      size_hint=(0.95, 0.9))
        btn.bind(on_release=popup.dismiss)
        popup.open()

    # ── Layar utama ──────────────────────────────────────────────────────
    def _show_main(self):
        box = BoxLayout(orientation='vertical', padding=10, spacing=8)
        days = self.trial['days_left']
        box.add_widget(Label(
            text='IP Setter CCTV — Deteksi & Ubah IP Hikvision/EZVIZ',
            size_hint_y=None, height=30))
        box.add_widget(Label(
            text=(f'Versi percobaan s/d {legal.EXPIRY_TEXT} '
                  f'(sisa {days} hari)'),
            size_hint_y=None, height=22))

        row = BoxLayout(orientation='horizontal', size_hint_y=None,
                        height=52, spacing=8)
        self.scan_btn = Button(text='SCAN')
        self.scan_btn.bind(on_release=lambda *_: self.start_scan())
        self.stop_btn = Button(text='STOP', disabled=True)
        self.stop_btn.bind(on_release=lambda *_: self.stop_scan())
        disc_btn = Button(text='Disclaimer')
        disc_btn.bind(on_release=lambda *_: self._show_disclaimer_popup())
        row.add_widget(self.scan_btn)
        row.add_widget(self.stop_btn)
        row.add_widget(disc_btn)
        box.add_widget(row)

        self.status_label = _wrapped_label(
            'Tekan SCAN untuk mencari perangkat Hikvision/EZVIZ di '
            'jaringan ini. HP harus tersambung ke Wi-Fi/LAN yang sama '
            'dengan perangkat (satu domain broadcast).')
        box.add_widget(self.status_label)

        scroll = ScrollView(size_hint=(1, 1))
        self.cards_box = GridLayout(cols=1, size_hint_y=None, spacing=8,
                                    padding=(0, 4))
        self.cards_box.bind(
            minimum_height=self.cards_box.setter('height'))
        scroll.add_widget(self.cards_box)
        box.add_widget(scroll)
        self._swap(box)

    # ── Pemindaian ───────────────────────────────────────────────────────
    def start_scan(self):
        if self.scanning:
            return
        self.trial = legal.trial_status(self.state_dir)
        if self.trial['expired']:
            self._show_expired()
            return
        self.scanning = True
        self.devices = {}
        self.cards_box.clear_widgets()
        self.scan_btn.disabled = True
        self.stop_btn.disabled = False
        self.status_label.text = 'Memindai jaringan (SADP)…'
        self.stop_event = threading.Event()
        worker = threading.Thread(target=self._scan_worker, daemon=True)
        worker.start()

    def stop_scan(self):
        if self.stop_event is not None:
            self.stop_event.set()

    def _scan_worker(self):
        def _found(dev):
            Clock.schedule_once(
                lambda *_args, d=dev: self._add_device_card(d))
        devices, info = sadp_net.discover(
            timeout=6.0, stop_event=self.stop_event, on_device=_found)
        Clock.schedule_once(
            lambda *_args: self._finish_scan(devices, info))

    def _finish_scan(self, devices, info):
        self.scanning = False
        self.scan_btn.disabled = False
        self.stop_btn.disabled = True
        count = info.get('count', len(devices))
        lock = info.get('lock', {})
        lock_txt = 'OK' if lock.get('multicast') else '—'
        if info.get('error'):
            self.status_label.text = f"Pemindaian gagal: {info['error']}"
        elif count == 0:
            self.status_label.text = (
                f'Selesai: 0 perangkat ditemukan | RX {info.get("rx", 0)} '
                f'| MLock {lock_txt}. Pastikan perangkat menyala dan satu '
                'jaringan lokal dengan HP.')
        else:
            self.status_label.text = (
                f'Selesai: {count} perangkat ditemukan | '
                f'RX {info.get("rx", 0)} | MLock {lock_txt}. '
                'Ketuk "Ubah IP / DHCP" pada perangkat untuk mengubah '
                'parameter jaringannya.')

    def _add_device_card(self, dev):
        key = sadp_net._norm_mac(dev.get('mac', '')) or dev.get('ip', '')
        if key in self.devices:
            return
        self.devices[key] = dev
        dhcp = dev.get('dhcp')
        if dhcp is True:
            dhcp_txt = 'DHCP: Aktif'
        elif dhcp is False:
            dhcp_txt = 'DHCP: Nonaktif (IP statis)'
        else:
            dhcp_txt = 'DHCP: tidak diketahui'
        lines = [
            f"[b]{dev.get('model') or 'Perangkat Hikvision/EZVIZ'}[/b]",
            f"IP: {dev.get('ip') or '-'}    MAC: {dev.get('mac') or '-'}",
            f"Serial: {dev.get('serial') or '-'}",
            dhcp_txt + (f"    Mask: {dev.get('mask')}" if dev.get('mask') else '')
            + (f"    GW: {dev.get('gateway')}" if dev.get('gateway') else ''),
        ]
        if dev.get('firmware'):
            lines.append(f"Firmware: {dev['firmware']}")
        card = BoxLayout(orientation='vertical', size_hint_y=None,
                         height=168, padding=8, spacing=4)
        card.add_widget(_wrapped_label('\n'.join(lines), markup=True))
        btn = Button(text='Ubah IP / DHCP', size_hint_y=None, height=46)
        btn.bind(on_release=lambda *_a, d=dev: self._open_network_popup(d))
        card.add_widget(btn)
        self.cards_box.add_widget(card)

    # ── Popup ubah jaringan ──────────────────────────────────────────────
    def _open_network_popup(self, dev):
        box = BoxLayout(orientation='vertical', padding=10, spacing=8)
        box.add_widget(_wrapped_label(
            f"[b]{dev.get('model') or 'Perangkat'}[/b]\n"
            f"MAC: {dev.get('mac') or '-'}    "
            f"IP saat ini: {dev.get('ip') or '-'}", markup=True))

        dhcp_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                             height=40)
        dhcp_cb = CheckBox(active=bool(dev.get('dhcp')), size_hint_x=None,
                           width=48)
        dhcp_row.add_widget(dhcp_cb)
        dhcp_row.add_widget(Label(text='DHCP otomatis (IP dari router)',
                                  halign='left'))
        box.add_widget(dhcp_row)

        ip_in = TextInput(text=dev.get('ip') or '', multiline=False,
                          size_hint_y=None, height=44,
                          hint_text='Alamat IPv4 baru')
        mask_in = TextInput(text=dev.get('mask') or '255.255.255.0',
                            multiline=False, size_hint_y=None, height=44,
                            hint_text='Subnet mask')
        gw_in = TextInput(text=dev.get('gateway') or '', multiline=False,
                          size_hint_y=None, height=44,
                          hint_text='Gateway')
        for widget in (ip_in, mask_in, gw_in):
            box.add_widget(widget)

        def _toggle_dhcp(_cb, active):
            for widget in (ip_in, mask_in, gw_in):
                widget.disabled = bool(active)
        dhcp_cb.bind(active=_toggle_dhcp)
        _toggle_dhcp(dhcp_cb, dhcp_cb.active)

        pw_in = TextInput(
            text='', multiline=False, password=True, size_hint_y=None,
            height=44,
            hint_text='Kata sandi admin perangkat '
                      '(EZVIZ: Verification Code di stiker)')
        box.add_widget(pw_in)

        result_lbl = _wrapped_label(
            'Catatan: sebagian model/firmware dapat menolak perintah ini; '
            'balasan perangkat akan ditampilkan apa adanya.')
        box.add_widget(result_lbl)

        btn_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                            height=50, spacing=8)
        cancel_btn = Button(text='Batal')
        apply_btn = Button(text='Terapkan')
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(apply_btn)
        box.add_widget(btn_row)

        popup = Popup(title='Ubah IP / DHCP', content=box,
                      size_hint=(0.95, 0.92), auto_dismiss=False)
        cancel_btn.bind(on_release=popup.dismiss)

        def _apply(*_):
            trial = legal.trial_status(self.state_dir)
            if trial['expired']:
                result_lbl.text = ('Masa percobaan telah berakhir '
                                   f'({legal.EXPIRY_TEXT}).')
                return
            dhcp = bool(dhcp_cb.active)
            ip = ip_in.text.strip()
            mask = mask_in.text.strip()
            gateway = gw_in.text.strip()
            passwd = pw_in.text.strip()
            if not passwd:
                result_lbl.text = 'Kata sandi perangkat wajib diisi.'
                return
            if not dhcp:
                for name, value in (('IPv4', ip), ('Subnet mask', mask),
                                    ('Gateway', gateway)):
                    if value and not sadp_net.valid_ipv4(value):
                        result_lbl.text = f'{name} tidak valid: {value}'
                        return
                if not ip:
                    result_lbl.text = ('Mode statis memerlukan alamat IPv4. '
                                       'Aktifkan DHCP bila ingin otomatis.')
                    return
            apply_btn.disabled = True
            cancel_btn.disabled = True
            result_lbl.text = 'Mengirim perintah ke perangkat…'

            def _worker():
                res = sadp_net.apply_network_settings(
                    dev, passwd, ip, mask, gateway, dhcp)
                Clock.schedule_once(
                    lambda *_a: _apply_done(res))

            def _apply_done(res):
                apply_btn.disabled = False
                cancel_btn.disabled = False
                if res.get('ok'):
                    result_lbl.text = (
                        'BERHASIL — perangkat menerima pengaturan baru. '
                        'Tutup popup ini lalu tekan SCAN untuk memeriksa '
                        'IP terbarunya.')
                else:
                    msg = res.get('error') or 'Perintah gagal.'
                    if res.get('result'):
                        msg += f" (Result: {res['result']})"
                    result_lbl.text = 'GAGAL: ' + msg

            threading.Thread(target=_worker, daemon=True).start()

        apply_btn.bind(on_release=_apply)
        popup.open()


if __name__ == '__main__':
    IPSetterApp().run()
