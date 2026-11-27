[app]
title = KML SHP Converter
package.name = kmlshpconverter
package.domain = org.example

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,otf,kml,kmz
source.exclude_dirs = tests,bin,.git,__pycache__,_tmp_pkgs

version = 1.0.0
requirements = python3,kivy==2.3.1,androidstorage4kivy

orientation = landscape
fullscreen = 0

android.api = 34
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.allow_backup = True
android.accept_sdk_license = True
android.private_storage = True
android.presplash_color = #0f172a
icon.filename = %(source.dir)s/assets/icon.png

[buildozer]
log_level = 2
warn_on_root = 1
