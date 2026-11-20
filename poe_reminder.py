#!/usr/bin/env python3
"""PoE Reminder - one-shot reminders that fire the next time you enter a hideout.

Run without arguments to start the daemon. Client commands (talk to the daemon over D-Bus):
    poe_reminder.py prompt        show the input popup
    poe_reminder.py add TEXT...   add a reminder directly
    poe_reminder.py list          list pending reminders
    poe_reminder.py clear         drop all pending reminders
"""
import json
import os
import re
import sys
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"  # local, gitignored; falls back to config.default.json


def load_config():
    cfg = json.loads((HERE / "config.default.json").read_text())
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except FileNotFoundError:
        pass
    return cfg


CONFIG = load_config()
LOG_FILE = Path(CONFIG["log_file"]).expanduser()
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "poe-reminder/reminders.json"
POLL_MS = int(CONFIG["poll_ms"])
TRIGGER = re.compile(CONFIG["trigger_regex"], re.IGNORECASE)
DIALOG_TITLE = "PoE Reminder"  # the KWin script matches on this caption

BUS_NAME = "local.PoeReminder"
OBJ_PATH = "/local/PoeReminder"
IFACE_XML = f"""
<node>
  <interface name="{BUS_NAME}">
    <method name="Prompt"/>
    <method name="Add"><arg type="s" name="text" direction="in"/></method>
    <method name="List"><arg type="as" name="items" direction="out"/></method>
    <method name="Clear"/>
  </interface>
</node>
"""


def log(msg):
    print(msg, flush=True)


class Reminders:
    def __init__(self):
        self.items = []
        try:
            self.items = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            pass

    def save(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=1))
        tmp.replace(STATE_FILE)

    def add(self, text):
        text = text.strip()
        if text:
            self.items.append(text)
            self.save()
            log(f"added reminder: {text!r} ({len(self.items)} pending)")

    def pop_all(self):
        items, self.items = self.items, []
        self.save()
        return items


class LogTailer:
    """Polls the log file, calling on_line for each newly appended complete line."""

    def __init__(self, path, on_line):
        self.path, self.on_line = path, on_line
        self.fh = None
        self.inode = None
        self.buf = b""
        self._open(from_end=True)

    def _open(self, from_end):
        if self.fh:
            self.fh.close()
            self.fh = None
        try:
            self.fh = open(self.path, "rb")
        except OSError:
            return
        self.inode = os.fstat(self.fh.fileno()).st_ino
        if from_end:
            self.fh.seek(0, os.SEEK_END)
        self.buf = b""

    def poll(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return True
        if self.fh is None or st.st_ino != self.inode:
            self._open(from_end=False)  # file appeared or was replaced: read it from the start
        elif st.st_size < self.fh.tell():  # truncated
            self.fh.seek(0)
            self.buf = b""
        if self.fh is None:
            return True
        data = self.fh.read()
        if data:
            self.buf += data
            *lines, self.buf = self.buf.split(b"\n")
            for raw in lines:
                self.on_line(raw.decode("utf-8", "replace").rstrip("\r"))
        return True  # keep GLib timeout alive


class Daemon:
    def __init__(self):
        self.reminders = Reminders()
        self.dialog_open = False
        self.tailer = LogTailer(LOG_FILE, self.on_line)
        GLib.timeout_add(POLL_MS, self.tailer.poll)
        node = Gio.DBusNodeInfo.new_for_xml(IFACE_XML)
        self.iface = node.interfaces[0]
        Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                         self.on_bus, None, self.on_name_lost)
        log(f"watching {LOG_FILE}; {len(self.reminders.items)} reminder(s) pending")

    # --- D-Bus -------------------------------------------------------------
    def on_bus(self, conn, _name):
        conn.register_object(OBJ_PATH, self.iface, self.on_call, None, None)

    def on_name_lost(self, _conn, _name):
        log("could not own D-Bus name (already running?)")
        sys.exit(1)

    def on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "Prompt":
            self.prompt()
            invocation.return_value(None)
        elif method == "Add":
            self.reminders.add(params.unpack()[0])
            invocation.return_value(None)
        elif method == "List":
            invocation.return_value(GLib.Variant("(as)", (list(self.reminders.items),)))
        elif method == "Clear":
            self.reminders.pop_all()
            invocation.return_value(None)

    # --- popup -------------------------------------------------------------
    def prompt(self):
        if self.dialog_open:
            return
        pending = len(self.reminders.items)
        label = "Remind me next time I enter a hideout:"
        if pending:
            label += f"\n({pending} already pending)"
        try:
            proc = Gio.Subprocess.new(
                ["kdialog", "--title", DIALOG_TITLE, "--inputbox", label, ""],
                Gio.SubprocessFlags.STDOUT_PIPE,
            )
        except GLib.Error as e:
            log(f"failed to start kdialog: {e.message}")
            return
        self.dialog_open = True
        proc.communicate_utf8_async(None, None, self.on_dialog_done, None)

    def on_dialog_done(self, proc, result, _data):
        self.dialog_open = False
        try:
            _ok, out, _err = proc.communicate_utf8_finish(result)
        except GLib.Error:
            return
        if proc.get_exit_status() == 0 and out:
            self.reminders.add(out)

    # --- trigger -----------------------------------------------------------
    def on_line(self, line):
        if not TRIGGER.search(line) or not self.reminders.items:
            return
        items = self.reminders.pop_all()
        log(f"hideout entered, firing {len(items)} reminder(s)")
        body = "\n".join(f"• {t}" for t in items)
        notify("PoE Reminder", body)


def notify(summary, body):
    try:
        Gio.Subprocess.new(
            ["notify-send", "--app-name=PoE Reminder", "--urgency=critical",
             "--icon=appointment-soon", summary, body],
            Gio.SubprocessFlags.NONE,
        )
    except GLib.Error as e:
        log(f"notify-send failed: {e.message}")


def client(cmd, args):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(method, params=None, reply=None):
        return bus.call_sync(BUS_NAME, OBJ_PATH, BUS_NAME, method, params,
                             GLib.VariantType(reply) if reply else None,
                             Gio.DBusCallFlags.NONE, -1, None)

    try:
        if cmd == "prompt":
            call("Prompt")
        elif cmd == "add":
            call("Add", GLib.Variant("(s)", (" ".join(args),)))
        elif cmd == "list":
            for item in call("List", reply="(as)").unpack()[0]:
                print(item)
        elif cmd == "clear":
            call("Clear")
        else:
            print(__doc__)
            return 2
    except GLib.Error as e:
        print(f"error talking to daemon (is it running?): {e.message}", file=sys.stderr)
        return 1
    return 0


def main():
    if len(sys.argv) > 1:
        return client(sys.argv[1], sys.argv[2:])
    Daemon()
    GLib.MainLoop().run()


if __name__ == "__main__":
    sys.exit(main())
