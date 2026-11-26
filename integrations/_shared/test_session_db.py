"""Each pytest session gets its own *_test database and drops it (ticket 09).

CI and local runs share one MONGODB_TEST_DB prefix, so these tests run real
inner pytest sessions against a copy of integrations/conftest.py and watch
what they do to the cluster. Needs MONGODB_URI.
"""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pymongo

_ROOT_CONFTEST = Path(__file__).parents[1] / "conftest.py"

# Two sessions meet here, so each checks its database while the other is
# alive and has written to its own.
_INNER_TEST = """
import os
import time
from pathlib import Path


def test_writes_only_to_its_own_database(db):
    name = os.environ["MONGODB_DB"]
    assert db.name == name
    db.marker.insert_one({"session": name})
    meeting = Path(os.environ["SESSION_MEETING"])
    (meeting / name).write_text("")
    deadline = time.monotonic() + 60
    while len(list(meeting.iterdir())) < 2 and time.monotonic() < deadline:
        time.sleep(0.2)
    assert len(list(meeting.iterdir())) == 2
    assert db.marker.count_documents({}) == 1
"""


def _session_dir(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    shutil.copy(_ROOT_CONFTEST, session / "conftest.py")
    (session / "test_inner.py").write_text(_INNER_TEST)
    return session


def _start_session(session: Path, test_db: str, meeting: Path) -> subprocess.Popen:
    pythonpath = os.pathsep.join(
        filter(None, [str(_ROOT_CONFTEST.parent), os.environ.get("PYTHONPATH")])
    )
    env = {
        **os.environ,
        "MONGODB_TEST_DB": test_db,
        "SESSION_MEETING": str(meeting),
        "PYTHONPATH": pythonpath,
    }
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(session),
            str(session / "test_inner.py"),
        ],
        cwd=session,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_concurrent_sessions_use_and_drop_only_their_own_database(tmp_path):
    prefix = os.environ["MONGODB_TEST_DB"]
    session = _session_dir(tmp_path)
    meeting = tmp_path / "meeting"
    meeting.mkdir()
    client = pymongo.MongoClient(os.environ["MONGODB_URI"])
    sibling = client[f"{prefix[: -len('_test')]}_sibling_{uuid.uuid4().hex[:8]}_test"]
    sibling.marker.insert_one({"session": "sibling"})
    names: list[str] = []
    try:
        runs = [_start_session(session, prefix, meeting) for _ in range(2)]
        outputs = [run.communicate(timeout=180)[0] for run in runs]
        names = sorted(path.name for path in meeting.iterdir())

        assert [run.returncode for run in runs] == [0, 0], outputs
        assert len(set(names)) == 2
        for name in names:
            assert name.endswith("_test")
            assert name.startswith(prefix[: -len("_test")] + "_")
        assert not set(names) & set(client.list_database_names())
        assert sibling.marker.count_documents({}) == 1
    finally:
        for name in names:
            client[name].marker.drop()
        client.drop_database(sibling.name)
        client.close()


def test_a_session_still_refuses_a_database_without_the_test_marker(tmp_path):
    session = _session_dir(tmp_path)
    meeting = tmp_path / "meeting"
    meeting.mkdir()

    run = _start_session(session, "hyperadar", meeting)
    output = run.communicate(timeout=180)[0]

    assert run.returncode != 0
    assert "refusing to run against production" in output
    assert list(meeting.iterdir()) == []
