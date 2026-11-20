"""Fake PyTorch (ROCm build with one AMD GPU) for the Linux/Windows scenarios.
FAKE_TORCH_BROKEN=1: the GPU is visible but the first calculation fails, as with a card ROCm does not support."""
import os
from types import SimpleNamespace

__version__ = "2.9.1+rocm6.4"
version = SimpleNamespace(hip="6.4.43482", cuda=None)


class _Tensor:
    def __init__(self, v):
        self.v = v

    def __matmul__(self, other):
        return _Tensor(self.v * other.v)

    def abs(self):
        return _Tensor(abs(self.v))

    def sum(self):
        return _Tensor(self.v * 1024 * 1024)

    def item(self):
        return self.v


def randn(*shape, device=None):
    if os.environ.get("FAKE_TORCH_BROKEN") == "1":
        raise RuntimeError("HIP error: invalid device function\nCompile with TORCH_USE_HIP_DSA to enable device-side assertions.")
    return _Tensor(0.5)


class _Cuda:
    @staticmethod
    def is_available():
        return True

    @staticmethod
    def get_device_name(i=0):
        return "AMD Radeon RX 7900 XTX"

    @staticmethod
    def get_device_properties(i=0):
        return SimpleNamespace(total_memory=24 * 1024 ** 3, major=11, minor=0, gcnArchName="gfx1100")

    @staticmethod
    def synchronize():
        pass


cuda = _Cuda()
backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
