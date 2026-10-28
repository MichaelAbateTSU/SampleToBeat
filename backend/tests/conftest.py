import os
import tempfile

# isolate every test run from the user's real projects (DATA_DIR is read at import time)
os.environ["CHOPLAB_DATA"] = tempfile.mkdtemp(prefix="choplab_test_")
os.environ["CHOPLAB_FRONTEND"] = os.path.join(os.environ["CHOPLAB_DATA"], "no_frontend")

import numpy as np  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(scope="session")
def demo_audio():
    from choplab import demo

    return demo.make()


@pytest.fixture(scope="session")
def demo_analysis(demo_audio):
    from choplab import analysis

    return analysis.analyze(demo_audio)


def dominant_hz(y: np.ndarray, sr: int = 48000) -> float:
    m = y.mean(axis=1) if y.ndim == 2 else y
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    return float(np.fft.rfftfreq(len(m), 1 / sr)[np.argmax(spec)])
