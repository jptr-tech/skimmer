import logging

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BUSY_MESSAGE = "Device is busy — another program has files open"


def find_mount_for_path(mounts, mount_path):
    """Return the Gio.Mount whose root is ``mount_path``, or None."""
    if not mount_path:
        return None
    for mount in mounts:
        root = mount.get_root()
        if root is not None and root.get_path() == mount_path:
            return mount
    return None


def friendly_unmount_error(exc):
    """Turn a GIO unmount error into a message worth showing the user."""
    try:
        if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.BUSY):
            return BUSY_MESSAGE
    except Exception:
        pass
    message = getattr(exc, "message", None)
    return message or str(exc)


def unmount_mount(mount, on_done):
    """Unmount ``mount`` asynchronously.

    ``on_done(ok, message)`` is invoked once the operation finishes, with a
    human-readable ``message`` when ``ok`` is false. Must be called on the main
    thread; the callback also runs on the main loop.
    """
    operation = Gio.MountOperation()

    def _finished(m, result, _operation=operation):
        try:
            m.unmount_with_operation_finish(result)
        except GLib.Error as exc:
            log.warning(f"[skimmer] Eject failed: {exc.message}")
            on_done(False, friendly_unmount_error(exc))
            return
        on_done(True, None)

    try:
        mount.unmount_with_operation(
            Gio.MountUnmountFlags.NONE,
            operation,
            None,
            _finished,
        )
    except GLib.Error as exc:
        log.warning(f"[skimmer] Eject could not start: {exc.message}")
        on_done(False, friendly_unmount_error(exc))
