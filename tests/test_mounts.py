from gi.repository import Gio, GLib

from skimmer.mounts import (
    BUSY_MESSAGE,
    find_mount_for_path,
    friendly_unmount_error,
    unmount_mount,
)


class FakeRoot:
    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path


class FakeMount:
    def __init__(self, path, error=None):
        self._root = FakeRoot(path)
        self._error = error
        self.unmounted = False

    def get_root(self):
        return self._root

    def unmount_with_operation(self, flags, operation, cancellable, callback):
        self.unmounted = True
        callback(self, None)

    def unmount_with_operation_finish(self, result):
        if self._error is not None:
            raise self._error


class TestFindMountForPath:
    def test_finds_matching_path(self):
        match = FakeMount("/run/media/user/disk")
        mounts = [FakeMount("/other"), match]
        assert find_mount_for_path(mounts, "/run/media/user/disk") is match

    def test_returns_none_when_absent(self):
        assert find_mount_for_path([FakeMount("/other")], "/run/media/user/disk") is None

    def test_empty_path_returns_none(self):
        assert find_mount_for_path([FakeMount("/x")], "") is None


class TestFriendlyUnmountError:
    def test_busy_maps_to_message(self):
        err = GLib.Error("busy", Gio.io_error_quark(), Gio.IOErrorEnum.BUSY)
        assert friendly_unmount_error(err) == BUSY_MESSAGE

    def test_other_error_passes_through(self):
        err = GLib.Error("nope", Gio.io_error_quark(), Gio.IOErrorEnum.FAILED)
        assert friendly_unmount_error(err) == "nope"


class TestUnmountMount:
    def test_success_calls_back_true(self):
        mount = FakeMount("/run/media/user/disk")
        results = []
        unmount_mount(mount, lambda ok, msg: results.append((ok, msg)))
        assert mount.unmounted
        assert results == [(True, None)]

    def test_busy_reports_busy_message(self):
        err = GLib.Error("busy", Gio.io_error_quark(), Gio.IOErrorEnum.BUSY)
        mount = FakeMount("/run/media/user/disk", error=err)
        results = []
        unmount_mount(mount, lambda ok, msg: results.append((ok, msg)))
        assert results == [(False, BUSY_MESSAGE)]
