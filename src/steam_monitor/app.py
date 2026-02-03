"""Точка входа приложения и сборка зависимостей."""

import asyncio
import logging
import sys
from pathlib import Path

from .config import AppConfig, ConfigError, ConfigLoader
from .monitor import ConsoleRenderer, SteamDownloadMonitor
from .steam import AppManifestStore, ContentLogParser, ContentLogReader, SteamLibrary, SteamLocator, VdfKeyValueParser


def main() -> int:
    """Запускает мониторинг и возвращает код завершения."""
    loader = ConfigLoader()
    try:
        config = loader.load()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        logging.getLogger("steam_monitor.output").error(str(exc))
        return 1

    output_logger, debug_logger = _configure_logging(config)

    steam_root = config.steam_root
    if not steam_root:
        locator = SteamLocator(
            config.registry_paths,
            config.registry_values,
            config.steam_root_candidates,
            config.steam_log_rel_path,
        )
        steam_root = locator.find_install_path()

    if not steam_root:
        output_logger.error("Steam install path not found.")
        return 1

    steam_root_path = Path(steam_root)
    log_path = steam_root_path / config.steam_log_rel_path
    library_vdf_path = steam_root_path / config.library_vdf_rel_path

    library_provider = SteamLibrary(steam_root_path, library_vdf_path)
    reader = ContentLogReader(log_path, config.tail_bytes, config.log_encoding)
    parser = ContentLogParser()
    manifest_store = AppManifestStore(VdfKeyValueParser(), config.manifest_pattern)
    renderer = ConsoleRenderer(
        titles={
            "downloading": "Загрузка",
            "paused": "На паузе",
            "queued": "В очереди",
            "unknown": "Неизвестно",
        },
        order=("downloading", "paused", "queued", "unknown"),
    )

    monitor = SteamDownloadMonitor(
        reader=reader,
        parser=parser,
        library_provider=library_provider,
        manifest_store=manifest_store,
        renderer=renderer,
        logger=output_logger,
        debug_logger=debug_logger,
        interval_seconds=config.interval_seconds,
        samples=config.samples,
        timestamp_format=config.timestamp_format,
    )

    try:
        return asyncio.run(monitor.run())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        output_logger.error(f"Unexpected error: {exc}")
        return 1


def _configure_logging(config: AppConfig) -> tuple[logging.Logger, logging.Logger]:
    """Настраивает логгеры вывода и отладки."""
    output_logger = logging.getLogger("steam_monitor.output")
    output_logger.handlers.clear()
    output_logger.setLevel(logging.INFO)
    output_logger.propagate = False
    output_handler = logging.StreamHandler(sys.stdout)
    output_handler.setLevel(logging.INFO)
    output_handler.setFormatter(logging.Formatter("%(message)s"))
    output_logger.addHandler(output_handler)

    debug_logger = logging.getLogger("steam_monitor.debug")
    debug_logger.handlers.clear()
    debug_logger.propagate = False
    debug_level = getattr(logging, config.log_level, logging.INFO)
    debug_logger.setLevel(debug_level)
    debug_formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
    debug_handler = logging.StreamHandler(sys.stdout)
    debug_handler.setLevel(debug_level)
    debug_handler.setFormatter(debug_formatter)
    debug_logger.addHandler(debug_handler)

    if config.log_file:
        try:
            file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
            file_handler.setLevel(debug_level)
            file_handler.setFormatter(debug_formatter)
            debug_logger.addHandler(file_handler)
        except OSError as exc:
            output_logger.error(f"Failed to open log file: {exc}")

    return output_logger, debug_logger
