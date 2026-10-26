#!/bin/sh
# Start IBus with Avro Phonetic (Bengali) and keep it as a login input source.
# Installed system-wide; run at login per user via /etc/xdg/autostart entry.
ibus-daemon -drx >/dev/null 2>&1
sleep 1

cur=$(gsettings get org.gnome.desktop.input-sources sources 2>/dev/null)
case "$cur" in
  *ibus-avro*) ;;
  *)
    gsettings set org.gnome.desktop.input-sources sources \
      "[('xkb', 'us'), ('ibus', 'ibus-avro')]" 2>/dev/null || true
    ;;
esac

ibus engine ibus-avro >/dev/null 2>&1 || true
