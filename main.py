# -*- coding: utf-8 -*-
"""
IP Setter CCTV (Trial) — aplikasi Android untuk mendeteksi perangkat
Hikvision / EZVIZ di jaringan lokal (protokol SADP) dan mengubah
parameter jaringannya (DHCP / IP statis) langsung dari HP, tanpa PC.

Perangkat BEDA SEGMEN IP tetap terdeteksi selama masih satu jaringan
fisik / Wi-Fi yang sama (satu domain broadcast) — sama seperti CCTV
Scanner; yang tidak bisa hanya bila terpisah router/VLAN.

VERSI PERCOBAAN: berlaku sampai 20 Januari 2027 (lihat src/legal.py).
Aplikasi independen — bukan produk resmi Hikvision/EZVIZ.

Tampilan meniru CCTV Scanner: tema gelap #1a1a2e, kartu #16213e,
aksen cyan #00d4ff, tombol SCAN biru / STOP merah.
"""

import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex
from kivy.core.window import Window

from src import legal
from src import sadp_net

Window.clearcolor = get_color_from_hex('#1a1a2e')


def make_label(text, font_size='13sp', color='#e0e0e0', bold=False,
               height=None, markup=False):
    lbl = Label(
        text=text,
        markup=markup,
        font_size=font_size,
        bold=bold,
        size_hint_y=None,
        height=height if height else dp(24),
        color=get_color_from_hex(color),
        halign='left',
        valign='middle',
    )
    lbl.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
    lbl.bind(texture_size=lambda inst, ts: setattr(inst, 'height', max(dp(24), ts[1])))
    return lbl


def make_input(text='', hint='', is_passwd=False):
    return TextInput(
        text=text,
        hint_text=hint,
        multiline=False,
        password=is_passwd,
        size_hint_y=None,
        height=dp(46),
        font_size='14sp',
        background_color=get_color_from_hex('#0f3460'),
        foreground_color=get_color_from_hex('#ffffff'),
        hint_text_color=get_color_from_hex('#888888'),
        cursor_color=get_color_from_hex('#00d4ff'),
        padding=[dp(10), dp(12), dp(10), dp(6)],
    )


def _disclaimer_scroll():
    scroll = ScrollView(size_hint=(1, 1))
    scroll.add_widget(make_label(legal.DISCLAIMER_TEXT, font_size='12sp',
                                 color='#e0e0e0'))
    return scroll


class DeviceCard(BoxLayout):
    """Kartu perangkat — gaya sama persis dengan DeviceCard CCTV Scanner."""

    def __init__(self, device, on_edit=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = [dp(10), dp(8), dp(10), dp(8)]
        self.spacing = dp(4)
        self.size_hint_y = None

        from kivy.graphics import Color, RoundedRectangle
        with self.canvas.before:
            Color(*get_color_from_hex('#16213e'))
            self.rect = RoundedRectangle(pos=self.pos, size=self.size,
                                         radius=[dp(8)])
        self.bind(pos=self._update_rect, size=self._update_rect)

        ip = device.get('ip') or '-'
        self.add_widget(make_label(
            text=f'[b][color=00d4ff]● {ip}[/color][/b]',
            font_size='16sp', markup=True, height=dp(28),
        ))
        self.add_widget(make_label(
            text='[b][color=f39c12]⚡ SADP/UDP[/color][/b]',
            font_size='11sp', markup=True, color='#f39c12',
        ))
        self.add_widget(make_label(
            text=f"[b]Model:[/b]  {device.get('model') or '-'}",
            font_size='13sp', markup=True, color='#cccccc',
        ))
        if device.get('mac'):
            self.add_widget(make_label(
                text=f"[b]MAC:[/b]  {device['mac']}",
                font_size='12sp', markup=True, color='#aaaaaa',
            ))
        if device.get('serial'):
            self.add_widget(make_label(
                text=f"[b]Serial:[/b]  {device['serial']}",
                font_size='12sp', markup=True, color='#aaaaaa',
            ))

        dhcp = device.get('dhcp')
        if dhcp is True:
            dhcp_txt = '[b]DHCP:[/b]  [color=90ee90]Aktif[/color]'
        elif dhcp is False:
            dhcp_txt = '[b]DHCP:[/b]  [color=f39c12]Nonaktif (IP statis)[/color]'
        else:
            dhcp_txt = '[b]DHCP:[/b]  tidak diketahui'
        self.add_widget(make_label(
            text=dhcp_txt, font_size='12sp', markup=True, color='#e0e0e0',
        ))
        net_bits = []
        if device.get('mask'):
            net_bits.append(f"Mask: {device['mask']}")
        if device.get('gateway'):
            net_bits.append(f"GW: {device['gateway']}")
        if net_bits:
            self.add_widget(make_label(
                text='     '.join(net_bits),
                font_size='12sp', markup=True, color='#aaaaaa',
            ))
        if device.get('firmware'):
            self.add_widget(make_label(
                text=f"[b]Firmware:[/b]  {device['firmware']}",
                font_size='11sp', markup=True, color='#888888',
            ))

        edit_btn = Button(
            text='Ubah IP / DHCP',
            font_size='13sp',
            size_hint_y=None,
            height=dp(38),
            background_color=get_color_from_hex('#e67e22'),
            color=get_color_from_hex('#ffffff'),
        )
        if on_edit is not None:
            edit_btn.bind(on_release=lambda *_: on_edit(device))
        self.add_widget(edit_btn)

        self.bind(minimum_height=self._set_height)
        self.height = self._calc_min_height()

    def _calc_min_height(self):
        total = self.padding[1] + self.padding[3]
        for child in self.children:
            total += child.height + self.spacing
        return max(total, dp(100))

    def _set_height(self, instance, value):
        self.height = value + self.padding[1] + self.padding[3]

    def _update_rect(self, *args):
        self.rect.pos = self.pos
        self.rect.size = self.size


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

    @staticmethod
    def _screen_box():
        return BoxLayout(orientation='vertical',
                         padding=[dp(12), dp(16), dp(12), dp(10)],
                         spacing=dp(8))

    # ── Layar masa percobaan berakhir ────────────────────────────────────
    def _show_expired(self):
        box = self._screen_box()
        box.add_widget(make_label('Masa Percobaan Berakhir',
                                  font_size='18sp', bold=True,
                                  color='#f39c12', height=dp(32)))
        box.add_widget(make_label(
            'Versi percobaan aplikasi ini berlaku sampai '
            f'{legal.EXPIRY_TEXT} dan sekarang sudah berakhir, sehingga '
            'aplikasi tidak dapat digunakan lagi.\n\n'
            'Terima kasih sudah mencoba.', font_size='13sp'))
        btn = Button(text='Lihat Disclaimer', size_hint_y=None,
                     height=dp(48), font_size='14sp',
                     background_color=get_color_from_hex('#0f3460'),
                     color=get_color_from_hex('#ffffff'))
        btn.bind(on_release=lambda *_: self._show_disclaimer_popup())
        box.add_widget(btn)
        self._swap(box)

    # ── Gerbang disclaimer (pertama kali) ────────────────────────────────
    def _show_disclaimer_gate(self):
        box = self._screen_box()
        title = Label(
            text='[b][color=00d4ff]IP Setter CCTV[/color][/b]',
            markup=True, font_size='22sp', size_hint_y=None,
            height=dp(44), halign='center', valign='middle')
        title.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        box.add_widget(title)
        box.add_widget(make_label('Versi Percobaan — Disclaimer',
                                  font_size='13sp', bold=True,
                                  color='#f39c12'))
        box.add_widget(_disclaimer_scroll())
        btn = Button(text='Saya Mengerti dan Setuju',
                     size_hint_y=None, height=dp(52), font_size='15sp',
                     bold=True,
                     background_color=get_color_from_hex('#007bff'),
                     color=get_color_from_hex('#ffffff'))
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
        box = BoxLayout(orientation='vertical', padding=dp(8),
                        spacing=dp(8))
        box.add_widget(_disclaimer_scroll())
        btn = Button(text='Tutup', size_hint_y=None, height=dp(48),
                     font_size='14sp',
                     background_color=get_color_from_hex('#6c757d'),
                     color=get_color_from_hex('#ffffff'))
        box.add_widget(btn)
        popup = Popup(title='Disclaimer', content=box,
                      size_hint=(0.95, 0.9),
                      background_color=get_color_from_hex('#16213e'))
        btn.bind(on_release=popup.dismiss)
        popup.open()

    # ── Layar utama ──────────────────────────────────────────────────────
    def _show_main(self):
        box = self._screen_box()

        header = Label(
            text='[b][color=00d4ff]IP Setter CCTV[/color][/b]',
            markup=True, font_size='22sp', size_hint_y=None,
            height=dp(40), halign='center', valign='middle')
        header.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        box.add_widget(header)
        subtitle = Label(
            text='Deteksi & Ubah IP Hikvision / EZVIZ',
            font_size='13sp', size_hint_y=None, height=dp(22),
            color=get_color_from_hex('#cccccc'),
            halign='center', valign='middle')
        subtitle.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        box.add_widget(subtitle)

        days = self.trial['days_left']
        trial_lbl = Label(
            text=(f'Versi percobaan s/d {legal.EXPIRY_TEXT} '
                  f'• sisa {days} hari'),
            font_size='12sp', size_hint_y=None, height=dp(22),
            color=get_color_from_hex('#f39c12'),
            halign='center', valign='middle')
        trial_lbl.bind(width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        box.add_widget(trial_lbl)

        btn_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                            height=dp(54), spacing=dp(8))
        self.scan_btn = Button(
            text='🔍  SCAN', font_size='18sp', bold=True,
            background_color=get_color_from_hex('#007bff'),
            color=get_color_from_hex('#ffffff'), size_hint_x=0.65)
        self.scan_btn.bind(on_release=lambda *_: self.start_scan())
        self.stop_btn = Button(
            text='■  STOP', font_size='15sp',
            background_color=get_color_from_hex('#dc3545'),
            color=get_color_from_hex('#ffffff'), disabled=True,
            size_hint_x=0.35)
        self.stop_btn.bind(on_release=lambda *_: self.stop_scan())
        btn_row.add_widget(self.scan_btn)
        btn_row.add_widget(self.stop_btn)
        box.add_widget(btn_row)

        disc_btn = Button(
            text='Disclaimer', size_hint_y=None, height=dp(36),
            font_size='12sp',
            background_color=get_color_from_hex('#0f3460'),
            color=get_color_from_hex('#ffffff'))
        disc_btn.bind(on_release=lambda *_: self._show_disclaimer_popup())
        box.add_widget(disc_btn)

        self.status_label = Label(
            text=('Tekan 🔍 SCAN untuk mencari perangkat '
                  'Hikvision/EZVIZ. Perangkat beda segmen IP tetap '
                  'terdeteksi selama masih satu jaringan fisik/Wi-Fi '
                  'yang sama — sama seperti CCTV Scanner.'),
            font_size='12sp', size_hint_y=None, height=dp(22),
            color=get_color_from_hex('#aaaaaa'),
            halign='left', valign='middle')
        self.status_label.bind(
            width=lambda inst, w: setattr(inst, 'text_size', (w, None)),
            texture_size=lambda inst, ts: setattr(inst, 'height', max(dp(22), ts[1])))
        box.add_widget(self.status_label)

        self.found_label = Label(
            text='Perangkat ditemukan: 0',
            font_size='13sp', bold=True, size_hint_y=None, height=dp(24),
            color=get_color_from_hex('#00d4ff'),
            halign='left', valign='middle')
        self.found_label.bind(
            width=lambda inst, w: setattr(inst, 'text_size', (w, None)))
        box.add_widget(self.found_label)

        scroll = ScrollView(size_hint=(1, 1))
        self.cards_box = GridLayout(cols=1, size_hint_y=None,
                                    spacing=dp(8), padding=(0, dp(4)))
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
        self.found_label.text = 'Perangkat ditemukan: 0'
        self.scan_btn.disabled = True
        self.stop_btn.disabled = False
        self.status_label.text = '🔍 Memindai jaringan (SADP)…'
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
            self.status_label.text = f"❌ Pemindaian gagal: {info['error']}"
        elif count == 0:
            self.status_label.text = (
                f'Selesai: 0 perangkat ditemukan | RX {info.get("rx", 0)} '
                f'| MLock {lock_txt}. Pastikan perangkat menyala dan '
                'tersambung ke jaringan fisik/Wi-Fi yang sama dengan HP '
                '(beda segmen IP tidak masalah; yang tidak bisa hanya '
                'bila terpisah router/VLAN).')
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
        card = DeviceCard(dev, on_edit=self._open_network_popup)
        self.cards_box.add_widget(card)
        self.found_label.text = f'Perangkat ditemukan: {len(self.devices)}'

    # ── Popup ubah jaringan ──────────────────────────────────────────────
    def _open_network_popup(self, dev):
        box = BoxLayout(orientation='vertical', padding=dp(10),
                        spacing=dp(8))
        box.add_widget(make_label(
            f"[b]{dev.get('model') or 'Perangkat Hikvision/EZVIZ'}[/b]",
            font_size='14sp', markup=True, color='#e0e0e0'))
        box.add_widget(make_label(
            f"MAC: {dev.get('mac') or '-'}    IP saat ini: {dev.get('ip') or '-'}",
            font_size='12sp', color='#aaaaaa'))

        dhcp_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                             height=dp(40), spacing=dp(6))
        dhcp_cb = CheckBox(active=bool(dev.get('dhcp')), size_hint_x=None,
                           width=dp(40))
        dhcp_row.add_widget(dhcp_cb)
        dhcp_row.add_widget(make_label(
            'DHCP otomatis (IP dari router)', font_size='13sp'))
        box.add_widget(dhcp_row)

        ip_in = make_input(text=dev.get('ip') or '',
                           hint='Alamat IPv4 baru')
        mask_in = make_input(text=dev.get('mask') or '255.255.255.0',
                             hint='Subnet mask')
        gw_in = make_input(text=dev.get('gateway') or '', hint='Gateway')
        for widget in (ip_in, mask_in, gw_in):
            box.add_widget(widget)

        def _toggle_dhcp(_cb, active):
            for widget in (ip_in, mask_in, gw_in):
                widget.disabled = bool(active)
        dhcp_cb.bind(active=_toggle_dhcp)
        _toggle_dhcp(dhcp_cb, dhcp_cb.active)

        pw_in = make_input(
            hint='Kata sandi admin perangkat '
                 '(EZVIZ: Verification Code di stiker)', is_passwd=True)
        box.add_widget(pw_in)

        result_lbl = make_label(
            'Catatan: sebagian model/firmware dapat menolak perintah '
            'ini; balasan perangkat akan ditampilkan apa adanya.',
            font_size='11sp', color='#f39c12')
        box.add_widget(result_lbl)

        btn_row = BoxLayout(orientation='horizontal', size_hint_y=None,
                            height=dp(50), spacing=dp(8))
        cancel_btn = Button(
            text='Batal', font_size='14sp',
            background_color=get_color_from_hex('#6c757d'),
            color=get_color_from_hex('#ffffff'))
        apply_btn = Button(
            text='Terapkan', font_size='14sp', bold=True,
            background_color=get_color_from_hex('#28a745'),
            color=get_color_from_hex('#ffffff'))
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(apply_btn)
        box.add_widget(btn_row)

        popup = Popup(title='Ubah IP / DHCP', content=box,
                      size_hint=(0.95, 0.92), auto_dismiss=False,
                      background_color=get_color_from_hex('#16213e'))
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
                    result_lbl.text = ('Mode statis memerlukan alamat '
                                       'IPv4. Aktifkan DHCP bila ingin '
                                       'otomatis.')
                    return
            apply_btn.disabled = True
            cancel_btn.disabled = True
            result_lbl.text = '⏳ Mengirim perintah ke perangkat…'

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
                        '🎉 BERHASIL — perangkat menerima pengaturan '
                        'baru. Tutup popup ini lalu tekan SCAN untuk '
                        'memeriksa IP terbarunya.')
                else:
                    msg = res.get('error') or 'Perintah gagal.'
                    if res.get('result'):
                        msg += f" (Result: {res['result']})"
                    result_lbl.text = '❌ GAGAL: ' + msg

            threading.Thread(target=_worker, daemon=True).start()

        apply_btn.bind(on_release=_apply)
        popup.open()


if __name__ == '__main__':
    IPSetterApp().run()
