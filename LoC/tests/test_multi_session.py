"""複数人の同時計測（infer.py の LoCSession と server.py）で状態が混ざらないことを検証する。

モデルと特徴抽出は偽物に差し替え、「LoC = 窓の中の画像の平均輝度」を返すようにする。
別の接続のフレームが窓に混ざると、返ってくる LoC がその接続の輝度からずれる。
"""

import asyncio
import base64
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")
pytest.importorskip("mediapipe")
pytest.importorskip("transformers")
pytest.importorskip("websockets")

from websockets.asyncio.client import connect  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402

import infer  # noqa: E402
from MediaPipe_Hands.hand_feature_extractor import _filter_native_stderr  # noqa: E402
from presence import PresenceClassifier, PresenceGate  # noqa: E402
from recorder import SessionRecorder  # noqa: E402
from server import LoCServer  # noqa: E402

SEQ_LEN = 3


class FakeImageExtractor:
    def extract(self, frame):
        return np.full(768, float(frame.mean()), dtype=np.float32)


class FakeHandExtractor:
    instances = []

    def __init__(self):
        self.closed = False
        self.reset_count = 0
        FakeHandExtractor.instances.append(self)

    def extract(self, frame):
        return np.full(162, float(frame.mean()), dtype=np.float32)

    def reset(self):
        self.reset_count += 1

    def close(self):
        self.closed = True


class MeanModel:
    def __call__(self, img_feat, hand_feat):
        return img_feat.mean().reshape(1, 1)


@pytest.fixture
def engine(monkeypatch):
    FakeHandExtractor.instances = []
    monkeypatch.setattr(infer, "HandFeatureExtractor", FakeHandExtractor)
    fake = infer.LoCEngine.__new__(infer.LoCEngine)
    fake.device = torch.device("cpu")
    fake.seq_len = SEQ_LEN
    fake.model = MeanModel()
    fake.image_extractor = FakeImageExtractor()
    fake.inference_lock = threading.Lock()
    fake.presence = None
    return fake


def solid(value):
    return np.full((48, 64, 3), value, dtype=np.uint8)


def test_sessions_keep_separate_windows(engine):
    a, b = engine.new_session(), engine.new_session()
    for _ in range(SEQ_LEN - 1):
        assert a.push(solid(20)) is None
        assert b.push(solid(80)) is None
    assert a.push(solid(20)) == pytest.approx(20)
    assert b.push(solid(80)) == pytest.approx(80)


def test_reset_and_close_only_affect_own_session(engine):
    a, b = engine.new_session(), engine.new_session()
    a.push(solid(20))
    b.push(solid(80))

    a.reset()
    assert a.warmup_remaining == SEQ_LEN
    assert b.warmup_remaining == SEQ_LEN - 1
    assert b.hand_extractor.reset_count == 0

    a.close()
    assert a.hand_extractor.closed and not b.hand_extractor.closed


def test_sessions_can_run_from_many_threads(engine):
    errors = []

    def run(value):
        session = engine.new_session()
        for i in range(30):
            loc = session.push(solid(value))
            if i >= SEQ_LEN - 1 and loc != pytest.approx(value):
                errors.append((value, loc))

    threads = [threading.Thread(target=run, args=(10 * i,)) for i in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_presence_fork_shares_model_but_not_history(tmp_path):
    path = tmp_path / "presence.pt"
    torch.save({"model_state_dict": PresenceClassifier().state_dict()}, path)
    gate = PresenceGate(path, window=3, device=torch.device("cpu"))

    a, b = gate.fork(), gate.fork()
    a.push(np.zeros(768, dtype=np.float32))
    assert a.model is b.model is gate.model
    assert len(a._history) == 1 and len(b._history) == 0 and len(gate._history) == 0
    assert a._history.maxlen == b._history.maxlen == 3


def test_native_stderr_filter_is_not_entered_concurrently(tmp_path):
    active = 0
    peak = 0
    guard = threading.Lock()

    def run(index):
        nonlocal active, peak
        with open(tmp_path / f"{index}.log", "w+b") as buffer:
            with _filter_native_stderr(buffer):
                with guard:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.01)
                with guard:
                    active -= 1

    threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1


def test_recorders_started_in_same_second_use_separate_files(tmp_path):
    started = datetime(2026, 11, 7, 14, 5, 9)
    recorders = [SessionRecorder(tmp_path, started_at=started) for _ in range(8)]
    start = threading.Barrier(len(recorders))

    def run(recorder):
        start.wait()
        recorder.write_frame(solid(10), 1000)

    threads = [threading.Thread(target=run, args=(r,)) for r in recorders]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for recorder in recorders:
        recorder.close()
    assert len({r.video_path for r in recorders}) == len(recorders)


def jpeg_message(value, timestamp):
    ok, encoded = cv2.imencode(".jpg", solid(value))
    assert ok
    return json.dumps({"type": "frame", "timestamp": timestamp,
                       "image": base64.b64encode(encoded.tobytes()).decode("ascii")})


def test_server_measures_two_connections_at_once(engine, tmp_path):
    async def scenario():
        server = LoCServer(engine, interval=1.0, record_dir=tmp_path)
        async with serve(server.handle, "127.0.0.1", 0, max_size=8 * 1024 * 1024) as ws_server:
            port = ws_server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}/loc"
            async with connect(url) as first:
                assert json.loads(await first.recv())["remaining"] == SEQ_LEN
                # 2人目が来ても1人目は切れない
                async with connect(url) as second:
                    assert json.loads(await second.recv())["remaining"] == SEQ_LEN

                    replies = {first: [], second: []}
                    for i in range(SEQ_LEN):
                        timestamp = 2000 * (i + 1)
                        await first.send(jpeg_message(40, timestamp))
                        await second.send(jpeg_message(200, timestamp))
                        replies[first].append(json.loads(await first.recv()))
                        replies[second].append(json.loads(await second.recv()))

                    assert [r["type"] for r in replies[first]] == ["warmup", "warmup", "loc"]
                    assert [r["type"] for r in replies[second]] == ["warmup", "warmup", "loc"]
                    assert replies[first][-1]["loc"] == pytest.approx(40, abs=2)
                    assert replies[second][-1]["loc"] == pytest.approx(200, abs=2)

                # 2人目が切断しても1人目は計測を続けられる
                await first.send(jpeg_message(40, 2000 * (SEQ_LEN + 1)))
                assert json.loads(await first.recv())["loc"] == pytest.approx(40, abs=2)

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))

    assert len(FakeHandExtractor.instances) == 2
    assert all(extractor.closed for extractor in FakeHandExtractor.instances)
    assert len(list(tmp_path.rglob("*.mp4"))) == 2
    assert len(list(tmp_path.rglob("*.csv"))) == 2
