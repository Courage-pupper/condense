"""Platform layer. Only macOS (Apple Silicon) is implemented so far; Windows (NVIDIA) and Linux (AMD) follow."""
from condense.platform.mac import (  # noqa: F401
    APP_SUPPORT_DIR,
    LEGACY_APP_SUPPORT_DIR,
    KeychainSecret,
    MacAppInstaller,
    SystemProbe,
    setup_environment,
)
