// PoE Reminder KWin script.
// Registers a global shortcut that only acts while Path of Exile is the active window,
// and makes the reminder popup appear on top of the (fullscreen) game with focus.

function isPoE(w) {
    if (!w) return false;
    const cls = String(w.resourceClass).toLowerCase();
    return cls === "steam_app_238960" || cls.indexOf("pathofexile") !== -1;
}

registerShortcut("PoE Reminder: add reminder",
                 "PoE Reminder: add reminder (only while Path of Exile is focused)",
                 "Ctrl+Alt+R",
                 function () {
                     if (!isPoE(workspace.activeWindow)) return;
                     callDBus("local.PoeReminder", "/local/PoeReminder", "local.PoeReminder", "Prompt");
                 });

workspace.windowAdded.connect(function (w) {
    if (w.caption !== "PoE Reminder") return;
    w.keepAbove = true;
    w.onAllDesktops = true;
    workspace.activeWindow = w;
});
