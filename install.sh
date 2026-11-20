#!/bin/sh
# Installs/updates the KWin shortcut script and the systemd user service.
set -e
cd "$(dirname "$0")"
chmod +x poe_reminder.py
[ -f config.json ] || cp config.default.json config.json  # edit config.json for local overrides

# KWin script (provides the shortcut)
if kpackagetool6 --type=KWin/Script --show poe-reminder >/dev/null 2>&1; then
    kpackagetool6 --type=KWin/Script --upgrade kwin
else
    kpackagetool6 --type=KWin/Script --install kwin
fi
kwriteconfig6 --file kwinrc --group Plugins --key poe-reminderEnabled true
qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.unloadScript poe-reminder >/dev/null 2>&1 || true
qdbus6 org.kde.KWin /KWin reconfigure

# Daemon
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/poe-reminder.service" ~/.config/systemd/user/poe-reminder.service
ln -sf "$PWD/poe_reminder.py" ~/.local/bin/poe-reminder
systemctl --user daemon-reload
systemctl --user enable poe-reminder.service
systemctl --user restart poe-reminder.service
echo "Installed. Shortcut: Ctrl+Alt+R (change in System Settings > Keyboard > Shortcuts > KWin)."
