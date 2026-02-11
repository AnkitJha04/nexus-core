[app]
title = Nexus Core
package.name = nexuscore
package.domain = org.test
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.1

# ⚠️ CUSTOM ICON SETTING
icon.filename = %(source.dir)s/logo.png

# ⚠️ LIBRARIES (Do not change)
requirements = python3,kivy==2.3.0,opencv,numpy

orientation = landscape
osx.python_version = 3
osx.kivy_version = 2.3.0
fullscreen = 1
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CAMERA
android.api = 33
android.minapi = 21
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 1