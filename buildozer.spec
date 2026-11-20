[app]
title = IP Setter CCTV (Trial)
package.name = ipsetter
package.domain = com.jontumpil

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt
source.include_patterns = src/*.py,src/__init__.py

version = 0.1.1
# Hanya python3 dan kivy (tanpa pin versi kivy — p4a pakai recipe bawaan v2.3.0)
requirements = python3,kivy

orientation = portrait
fullscreen = 0

android.permissions = INTERNET, ACCESS_NETWORK_STATE, ACCESS_WIFI_STATE, CHANGE_WIFI_STATE, CHANGE_NETWORK_STATE, CHANGE_WIFI_MULTICAST_STATE, WAKE_LOCK

android.api = 33
android.minapi = 21
android.ndk = 25b
android.accept_sdk_license = True

android.archs = arm64-v8a
android.release_artifact = apk

android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
bin.dir = ./bin
