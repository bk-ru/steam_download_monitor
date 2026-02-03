"""Загрузка и валидация конфигурации."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
import platform
from typing import Optional, Tuple


class ConfigError(Exception):
    """Ошибка загрузки или валидации конфигурации."""


@dataclass(frozen=True)
class AppConfig:
    """Проверенные значения конфигурации приложения."""
    interval_seconds: int
    samples: int
    tail_bytes: int
    log_level: str
    log_file: Optional[str]
    log_encoding: str
    timestamp_format: str
    steam_root: Optional[str]
    steam_log_rel_path: str
    library_vdf_rel_path: str
    manifest_pattern: str
    registry_paths: Tuple[str, ...]
    registry_values: Tuple[str, ...]
    steam_root_candidates: Tuple[str, ...]


class ConfigLoader:
    """Загружает конфигурацию из .env, JSON и переменных окружения."""
    def __init__(self, env_prefix: str = "STEAM_MONITOR_"):
        """Инициализирует загрузчик с префиксом переменных окружения."""
        self.env_prefix = env_prefix

    def load(self) -> AppConfig:
        """Загружает конфигурацию из .env, JSON и переменных окружения."""
        dotenv_path = os.environ.get(self.env_prefix + "DOTENV", ".env")
        self._load_dotenv(Path(dotenv_path))
        config_path = os.environ.get(self.env_prefix + "CONFIG", "config.json")
        data = self._read_json(Path(config_path))
        merged = self._merge_env(data)
        return self._build_config(merged)

    def _load_dotenv(self, path: Path) -> None:
        """Загружает переменные из .env без перезаписи уже заданных."""
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise ConfigError(f"Failed to read .env: {path}") from exc
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

    def _read_json(self, path: Path) -> dict:
        """Читает и парсит JSON-файл конфигурации."""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigError(f"Config file not found: {path}") from exc
        except OSError as exc:
            raise ConfigError(f"Failed to read config file: {path}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in config file: {path}") from exc
        if not isinstance(data, dict):
            raise ConfigError("Config root must be a JSON object")
        return data

    def _merge_env(self, data: dict) -> dict:
        """Сливает переменные окружения с конфигурацией."""
        merged = dict(data)
        registry = merged.get("registry", {}) if isinstance(merged.get("registry"), dict) else {}
        merged_registry = dict(registry)

        def env(name: str) -> Optional[str]:
            return os.environ.get(self.env_prefix + name)

        def env_list(name: str) -> Optional[list]:
            raw = env(name)
            if raw is None:
                return None
            parts = [p.strip() for p in raw.split(";") if p.strip()]
            return parts if parts else None

        simple_map = {
            "INTERVAL_SECONDS": "interval_seconds",
            "SAMPLES": "samples",
            "TAIL_BYTES": "tail_bytes",
            "LOG_LEVEL": "log_level",
            "LOG_FILE": "log_file",
            "LOG_ENCODING": "log_encoding",
            "TIMESTAMP_FORMAT": "timestamp_format",
            "STEAM_ROOT": "steam_root",
            "LOG_REL_PATH": "steam_log_rel_path",
            "LIBRARY_VDF_REL_PATH": "library_vdf_rel_path",
            "MANIFEST_PATTERN": "manifest_pattern",
        }
        for env_key, cfg_key in simple_map.items():
            value = env(env_key)
            if value is not None:
                merged[cfg_key] = value

        reg_paths = env_list("REGISTRY_PATHS")
        if reg_paths is not None:
            merged_registry["paths"] = reg_paths
        reg_values = env_list("REGISTRY_VALUES")
        if reg_values is not None:
            merged_registry["values"] = reg_values

        root_candidates = env_list("ROOT_CANDIDATES")
        if root_candidates is not None:
            merged["steam_root_candidates"] = root_candidates

        merged["registry"] = merged_registry
        return merged

    def _build_config(self, data: dict) -> AppConfig:
        """Валидирует и формирует экземпляр AppConfig."""
        interval_seconds = self._to_int(data.get("interval_seconds"), "interval_seconds", min_value=1)
        samples = self._to_int(data.get("samples"), "samples", min_value=1)
        tail_bytes = self._to_int(data.get("tail_bytes"), "tail_bytes", min_value=1)
        log_level = self._to_log_level(data.get("log_level"), "log_level")
        log_file = self._to_optional_str(data.get("log_file"))
        log_encoding = self._to_str(data.get("log_encoding"), "log_encoding")
        timestamp_format = self._to_str(data.get("timestamp_format"), "timestamp_format")
        steam_log_rel_path = self._to_str(data.get("steam_log_rel_path"), "steam_log_rel_path")
        library_vdf_rel_path = self._to_str(data.get("library_vdf_rel_path"), "library_vdf_rel_path")
        manifest_pattern = self._to_str(data.get("manifest_pattern"), "manifest_pattern")

        registry = data.get("registry") if isinstance(data.get("registry"), dict) else {}
        registry_paths = self._to_list(registry.get("paths"), "registry.paths")
        registry_values = self._to_list(registry.get("values"), "registry.values")
        steam_root_candidates = self._to_candidates(data.get("steam_root_candidates"))

        steam_root_raw = data.get("steam_root")
        steam_root = None
        if isinstance(steam_root_raw, str) and steam_root_raw.strip():
            steam_root = steam_root_raw.strip()

        return AppConfig(
            interval_seconds=interval_seconds,
            samples=samples,
            tail_bytes=tail_bytes,
            log_level=log_level,
            log_file=log_file,
            log_encoding=log_encoding,
            timestamp_format=timestamp_format,
            steam_root=steam_root,
            steam_log_rel_path=steam_log_rel_path,
            library_vdf_rel_path=library_vdf_rel_path,
            manifest_pattern=manifest_pattern,
            registry_paths=tuple(registry_paths),
            registry_values=tuple(registry_values),
            steam_root_candidates=tuple(steam_root_candidates),
        )

    def _to_int(self, value, name: str, min_value: Optional[int] = None) -> int:
        """Преобразует значение в int с минимальной границей."""
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid integer for {name}") from exc
        if min_value is not None and result < min_value:
            raise ConfigError(f"Value for {name} must be >= {min_value}")
        return result

    def _to_str(self, value, name: str) -> str:
        """Преобразует значение в непустую строку."""
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ConfigError(f"Missing or empty value for {name}")

    def _to_optional_str(self, value) -> Optional[str]:
        """Преобразует значение в строку или None."""
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _to_log_level(self, value, name: str) -> str:
        """Валидирует уровень логирования."""
        if isinstance(value, str) and value.strip():
            level = value.strip().upper()
            if level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
                return level
        raise ConfigError(f"Invalid log level for {name}")

    def _to_list(self, value, name: str) -> list:
        """Преобразует значение в непустой список строк."""
        if isinstance(value, list) and value:
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return items
        raise ConfigError(f"Missing or empty list for {name}")

    def _to_candidates(self, value) -> list:
        """Возвращает список кандидатов путей Steam."""
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, dict):
            key = self._platform_key()
            raw = value.get(key) or value.get("default") or []
            if isinstance(raw, list):
                return [str(item).strip() for item in raw if str(item).strip()]
        return []

    def _platform_key(self) -> str:
        """Нормализует имя платформы для конфигурации."""
        mapping = {
            "Windows": "windows",
            "Linux": "linux",
            "Darwin": "darwin",
        }
        return mapping.get(platform.system(), "default")
