"""Логика мониторинга загрузок Steam."""

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .steam import AppManifest, AppManifestStore, ContentLogParser, ContentLogReader, DownloadSnapshot, SteamLibrary


@dataclass(frozen=True)
class GameEntry:
    """Строка состояния игры."""
    name: str
    status: str
    rate: str
    appid: str
    downloaded_bytes: int
    total_bytes: int
    remaining_bytes: int


class ConsoleRenderer:
    """Формирует вывод по разделам статусов."""
    def __init__(self, titles: Dict[str, str], order: Tuple[str, ...]):
        """Инициализирует порядок и заголовки разделов."""
        self.titles = dict(titles)
        self.order = tuple(order)

    def render(self, timestamp: str, entries: Tuple[GameEntry, ...]) -> Tuple[str, ...]:
        """Возвращает строки вывода по статусам."""
        grouped: Dict[str, list] = {status: [] for status in self.order}
        for entry in entries:
            grouped.setdefault(entry.status, []).append(entry)

        lines = [f"[{timestamp}]"]
        for status in self.order:
            items = grouped.get(status) or []
            if not items:
                continue
            title = self.titles.get(status, status)
            lines.append(f"{title} ({len(items)})")
            for item in items:
                progress = self._format_progress(item.downloaded_bytes, item.total_bytes, item.remaining_bytes)
                lines.append(f"- {item.name} | Rate: {item.rate} | {progress}")

        if len(lines) == 1:
            lines.append("Нет активных загрузок")
        lines.append("")
        return tuple(lines)

    def _format_progress(self, downloaded: int, total: int, remaining: int) -> str:
        """Формирует строку прогресса загрузки."""
        if total <= 0:
            return "Progress: N/A"
        percent = int((downloaded / total) * 100) if total > 0 else 0
        return (
            f"Progress: {self._format_bytes(downloaded)} / {self._format_bytes(total)}"
            f" ({percent}%) | Remaining: {self._format_bytes(remaining)}"
        )

    def _format_bytes(self, value: int) -> str:
        """Форматирует байты в человекочитаемый вид."""
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{value} B"


class SteamDownloadMonitor:
    """Оркестратор получения и вывода информации о загрузке."""
    def __init__(
        self,
        reader: ContentLogReader,
        parser: ContentLogParser,
        library_provider: SteamLibrary,
        manifest_store: AppManifestStore,
        renderer: ConsoleRenderer,
        logger: logging.Logger,
        debug_logger: logging.Logger,
        interval_seconds: int,
        samples: int,
        timestamp_format: str,
    ):
        """Инициализирует монитор с зависимостями и параметрами."""
        self.reader = reader
        self.parser = parser
        self.library_provider = library_provider
        self.manifest_store = manifest_store
        self.renderer = renderer
        self.logger = logger
        self.debug_logger = debug_logger
        self.interval_seconds = interval_seconds
        self.samples = samples
        self.timestamp_format = timestamp_format
        self._last_snapshot: Optional[DownloadSnapshot] = None
        self._library_paths: Optional[Tuple] = None
        self._name_cache: Dict[str, str] = {}

    async def run(self) -> int:
        """Запускает мониторинг на заданное число сэмплов."""
        for index in range(self.samples):
            lines = await self.sample()
            for line in lines:
                self.logger.info(line)
            if index < self.samples - 1:
                await asyncio.sleep(self.interval_seconds)
        return 0

    async def sample(self) -> Tuple[str, ...]:
        """Считывает данные и формирует строки вывода."""
        text = await asyncio.to_thread(self.reader.read)
        snapshot = self.parser.parse(text, self._last_snapshot)
        self._last_snapshot = snapshot
        self.debug_logger.debug(
            "snapshot appid=%s status=%s rate=%s",
            snapshot.appid,
            snapshot.status,
            snapshot.rate,
        )
        library_paths = self._get_library_paths()
        manifests = await asyncio.to_thread(self.manifest_store.list_manifests, library_paths)
        self.debug_logger.debug("manifests total=%s", len(manifests))
        entries = self._build_entries(snapshot, manifests)
        timestamp = dt.datetime.now().strftime(self.timestamp_format)
        return self.renderer.render(timestamp, entries)

    def _build_entries(self, snapshot: DownloadSnapshot, manifests: Tuple[AppManifest, ...]) -> Tuple[GameEntry, ...]:
        """Формирует список записей по статусам."""
        all_by_appid = {m.appid: m for m in manifests}
        pending = [m for m in manifests if m.remaining_bytes() > 0]
        by_appid = {m.appid: m for m in pending}
        entries = []
        active_appid = snapshot.appid
        active_status = snapshot.status
        active_rate = "0 Mbps" if snapshot.status == "paused" else (snapshot.rate if snapshot.rate else "N/A")
        for manifest in pending:
            status = "queued"
            rate = "N/A"
            if active_appid and manifest.appid == active_appid:
                status = active_status
                rate = active_rate
            self._name_cache[manifest.appid] = manifest.name
            entries.append(
                GameEntry(
                    name=manifest.name,
                    status=status,
                    rate=rate,
                    appid=manifest.appid,
                    downloaded_bytes=manifest.bytes_downloaded,
                    total_bytes=manifest.bytes_to_download,
                    remaining_bytes=manifest.remaining_bytes(),
                )
            )

        if active_appid and active_appid not in by_appid:
            manifest = all_by_appid.get(active_appid)
            name = None
            downloaded = 0
            total = 0
            remaining = 0
            if manifest:
                name = manifest.name
                downloaded = manifest.bytes_downloaded
                total = manifest.bytes_to_download
                remaining = manifest.remaining_bytes()
                self._name_cache[active_appid] = manifest.name
            cached_name = self._name_cache.get(active_appid)
            if cached_name:
                name = cached_name
            if not name:
                name = f"AppID {active_appid}"
            rate = active_rate
            entries.append(
                GameEntry(
                    name=name,
                    status=active_status,
                    rate=rate,
                    appid=active_appid,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    remaining_bytes=remaining,
                )
            )

        return tuple(entries)

    def _get_library_paths(self) -> Tuple:
        """Лениво получает список библиотек Steam."""
        if self._library_paths is None:
            self._library_paths = self.library_provider.list_paths()
        return self._library_paths
