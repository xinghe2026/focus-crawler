"""配置包。"""

from .loader import load_config, load_config_dict, save_config
from .schema import ConfigError, TaskConfig

__all__ = ["TaskConfig", "ConfigError", "load_config", "load_config_dict", "save_config"]
