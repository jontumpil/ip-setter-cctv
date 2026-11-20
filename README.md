# IP Setter CCTV (Trial)

Aplikasi Android **independen** untuk teknisi/pengguna: mendeteksi
perangkat Hikvision / EZVIZ di jaringan lokal lewat protokol SADP dan
mengubah parameter jaringannya (**DHCP on/off, IPv4, subnet mask,
gateway**) langsung dari HP — tanpa laptop/PC dan tanpa EZVIZ Studio.

> Bukan produk resmi Hikvision/EZVIZ dan tidak berafiliasi dengan
> keduanya. Lihat disclaimer lengkap di dalam aplikasi
> (`src/legal.py`).

## Versi percobaan

Berlaku sampai **20 Januari 2027**. Setelah tanggal itu aplikasi
berhenti berfungsi (mekanisme anti-mundur-jam sederhana: tanggal
terakhir yang pernah dilihat aplikasi disimpan).

## Cara kerja

- **Discovery**: probe SADP `inquiry` (UDP 37020) dikirim ke multicast
  `239.255.255.250` + broadcast umum + directed broadcast tiap
  interface, dari listener INADDR_ANY + socket per-interface +
  MulticastLock Android — pola yang sama dengan yang terbukti menemukan
  perangkat beda segmen pada CCTV Scanner v1.9.1+. Balasan `ProbeMatch`
  membawa IP, MAC, model, serial, firmware, dan status DHCP perangkat.
- **Ubah jaringan**: pesan SADP `<Types>update</Types>` berisi MAC,
  kata sandi admin perangkat, IPv4/mask/gateway baru, dan flag DHCP.
  Format mengikuti implementasi terbuka
  [hikvision-tooling](https://github.com/cameronnewman/hikvision-tooling)
  (`internal/sadp/commands.go`) dan amplop tangkapan paket SADP Tool
  resmi. Perangkat membalas `ActionResponse` (`Result`:
  success/failure/denied) yang ditampilkan apa adanya.

## Batasan yang diketahui

- Kata sandi dikirim sebagai teks biasa di dalam pesan SADP, sama
  seperti implementasi rujukan. SADP Tool resmi versi baru mengirimnya
  dalam bentuk terenkripsi (atribut `bSalt`) — sebagian firmware
  terbaru **kemungkinan** menolak bentuk teks biasa. Hanya bisa
  dipastikan lewat uji perangkat nyata; aplikasi menampilkan balasan
  perangkat apa adanya bila ditolak.
- Hanya bekerja pada jaringan lokal yang sama (satu domain broadcast /
  L2) dengan perangkat — sama seperti SADP resmi; tidak menembus
  router/VLAN.
- Untuk EZVIZ, kata sandi yang dipakai adalah kata sandi perangkat /
  Verification Code pada stiker perangkat.

## Struktur

- `main.py` — UI Kivy (gerbang disclaimer, daftar perangkat, popup
  ubah IP/DHCP, penegakan masa percobaan).
- `src/sadp_net.py` — transport + discovery + perintah `update` SADP.
- `src/legal.py` — teks disclaimer + logika batas waktu.

## Build

Sama seperti CCTV Scanner: push ke `main` memicu GitHub Actions
(`buildozer android release`, APK ter-sign keystore rilis) dan
menerbitkan GitHub Release `v<version>` dari `buildozer.spec` ini.
