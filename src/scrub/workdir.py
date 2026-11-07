"""Private work folders for the levels that hand a file to another program.

The deep and full cleans of PDF, Word, MP3, M4A and FLAC write the ORIGINAL file --
metadata and all -- into a temporary folder for ffmpeg, LibreOffice or poppler to
read. `tempfile.TemporaryDirectory` removes it on every exception, but nothing runs
when the process is killed outright (`kill -9`, the machine running out of memory, a
closed laptop lid on a hung run), and the copy then stays in the shared temp folder
under a name that says what it is (November plan N2c).

So every such folder lives under ONE folder of this user's (`scrub-<uid>`, mode
0700: no other account can list it), and is named after the process that made it.
Each run first removes the folders whose process no longer exists. A killed run's
copy therefore lasts until the next run, readable by its owner alone -- and
`sweep()` can be called on its own.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile

_PREFIX = "pid"


def root() -> str:
    """This user's work root, created private if absent. Refuses one that is not a
    directory owned by this user with no access for others -- a folder someone else
    made under the same name could read the copies."""
    path = os.path.join(tempfile.gettempdir(), f"scrub-{os.getuid()}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077):
        raise PermissionError(f"{path} is not a private folder of this user's; "
                              "refusing to put copies of the input there")
    return path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                    # exists, owned by someone else
    return True


def sweep() -> int:
    """Remove the work folders of processes that no longer exist. Returns how many
    were removed. Never raises: a sweep that fails must not stop a clean."""
    removed = 0
    try:
        base = root()
        for name in os.listdir(base):
            head, _, _rest = name.partition("-")
            if not head.startswith(_PREFIX) or not head[len(_PREFIX):].isdigit():
                continue
            if not _alive(int(head[len(_PREFIX):])):
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
                removed += 1
    except OSError:
        pass
    return removed


@contextlib.contextmanager
def workdir(label: str):
    """A private folder for one external-program round trip, removed on exit."""
    path = tempfile.mkdtemp(prefix=f"{_PREFIX}{os.getpid()}-{label}-", dir=root())
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
