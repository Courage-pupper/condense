"""Platform layer: macOS (Apple Silicon), Linux and Windows (AMD ROCm / NVIDIA CUDA via PyTorch)."""
from condense.platform import detect  # noqa: F401
from condense.platform.detect import (  # noqa: F401
    DEFAULT_BACKEND,
    DEFAULT_ENCODER,
    GPU_DIARIZATION,
    HW_QUALITY_NAME,
    INSTALL_HINTS,
    IS_LINUX,
    IS_MAC_PLATFORM,
    IS_WINDOWS,
    PLATFORM,
    VIDEO_ENCODERS,
    WHISPER_BACKENDS,
    default_output_dir,
    log_file,
    open_command,
)
from condense.platform.linux import LinuxDesktopInstaller, SecretServiceSecret, has_ffmpeg_vaapi, vaapi_device  # noqa: F401
from condense.platform.windows import CredentialManagerSecret, WindowsShortcutInstaller, gpu_vendor  # noqa: F401
from condense.platform.mac import (  # noqa: F401
    APP_SUPPORT_DIR,
    LEGACY_APP_SUPPORT_DIR,
    KeychainSecret,
    MacAppInstaller,
    SystemProbe,
    setup_environment,
)

# The token store and the "install app" action of this platform
SecretStore = KeychainSecret if IS_MAC_PLATFORM else (CredentialManagerSecret if IS_WINDOWS else SecretServiceSecret)
AppInstaller = MacAppInstaller if IS_MAC_PLATFORM else (WindowsShortcutInstaller if IS_WINDOWS else LinuxDesktopInstaller)
