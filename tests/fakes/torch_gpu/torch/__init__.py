"""Fake PyTorch (ROCm build with one AMD GPU) for the Linux scenario."""
from types import SimpleNamespace

__version__ = "2.9.1+rocm6.4"
version = SimpleNamespace(hip="6.4.43482", cuda=None)


class _Cuda:
    @staticmethod
    def is_available():
        return True

    @staticmethod
    def get_device_name(i=0):
        return "AMD Radeon RX 7900 XTX"


cuda = _Cuda()
backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
