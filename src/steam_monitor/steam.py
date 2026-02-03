"""Модули для работы со Steam и чтения логов загрузки."""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

try:
    import winreg
except Exception:
    winreg = None


ROOT_MAP = {
    "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE if winreg else None,
    "HKLM": winreg.HKEY_LOCAL_MACHINE if winreg else None,
    "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER if winreg else None,
    "HKCU": winreg.HKEY_CURRENT_USER if winreg else None,
}


@dataclass(frozen=True)
class DownloadSnapshot:
    """Снимок состояния загрузки."""
    appid: Optional[str]
    rate: Optional[str]
    status: str


@dataclass(frozen=True)
class AppManifest:
    """Данные манифеста приложения Steam."""
    appid: str
    name: str
    bytes_downloaded: int
    bytes_to_download: int
    state_flags: int

    def remaining_bytes(self) -> int:
        """Возвращает объём оставшейся загрузки."""
        remaining = self.bytes_to_download - self.bytes_downloaded
        return remaining if remaining > 0 else 0


class VdfKeyValueParser:
    """Парсер простых пар ключ-значение формата VDF."""
    def __init__(self):
        """Инициализирует регулярное выражение парсинга."""
        self.kv_re = re.compile(r'^\s*"([^"]+)"\s+"([^"]*)"\s*$')

    def parse(self, text: str) -> Dict[str, str]:
        """Парсит текст и возвращает словарь ключ-значение."""
        result: Dict[str, str] = {}
        for line in text.splitlines():
            match = self.kv_re.match(line)
            if match:
                result[match.group(1)] = match.group(2)
        return result


class SteamLocator:
    """Ищет путь установки Steam через реестр, процессы и кандидаты путей."""
    def __init__(
        self,
        registry_paths: Iterable[str],
        registry_values: Iterable[str],
        root_candidates: Iterable[str],
        log_rel_path: str,
    ):
        """Инициализирует поиск с наборами ключей реестра и кандидатов."""
        self.registry_paths = tuple(registry_paths)
        self.registry_values = tuple(registry_values)
        self.root_candidates = tuple(root_candidates)
        self.log_rel_path = log_rel_path

    def find_install_path(self) -> Optional[str]:
        """Возвращает путь установки Steam или None."""
        candidates = []
        if winreg is not None:
            candidates.extend(self._find_from_registry())
        candidates.extend(self._find_from_process())
        candidates.extend(self._find_from_candidates())
        return self._pick_best_candidate(candidates)

    def _read_registry_value(self, root, subkey: str, value_name: str) -> Optional[str]:
        """Читает строковое значение из реестра."""
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return None

    def _find_from_registry(self) -> list:
        """Собирает кандидаты путей из реестра Windows."""
        candidates = []
        for reg_path in self.registry_paths:
            root_name, subkey = split_registry_path(reg_path)
            root = ROOT_MAP.get(root_name)
            if root is None or subkey is None:
                continue
            for value_name in self.registry_values:
                value = self._read_registry_value(root, subkey, value_name)
                if value:
                    candidates.append(Path(value))
        return candidates

    def _find_from_process(self) -> list:
        """Пытается определить путь Steam по запущенному процессу."""
        if os.name == "nt":
            return self._find_from_process_windows()
        return self._find_from_process_unix()

    def _find_from_process_windows(self) -> list:
        """Ищет Steam через процесс на Windows."""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-Process steam -ErrorAction SilentlyContinue).Path"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return []
        candidates = []
        for line in result.stdout.splitlines():
            value = line.strip().strip('"')
            if not value:
                continue
            path = Path(value)
            if path.exists():
                candidates.append(path.parent)
        return candidates

    def _find_from_process_unix(self) -> list:
        """Ищет Steam через процессы на Unix-like системах."""
        try:
            result = subprocess.run(
                ["ps", "-eo", "args"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return []
        candidates = []
        for line in result.stdout.splitlines():
            if "steam" not in line.lower():
                continue
            for token in line.split():
                if "steam" not in token.lower():
                    continue
                if not (token.startswith("/") or token.startswith("~")):
                    continue
                path = Path(os.path.expanduser(token))
                if path.name in {"steam", "steam.sh"} and path.exists():
                    candidates.append(path.parent)
        return candidates

    def _find_from_candidates(self) -> list:
        """Пробует найти Steam по известным путям."""
        candidates = []
        for candidate in self.root_candidates:
            expanded = os.path.expandvars(candidate)
            expanded = os.path.expanduser(expanded)
            path = Path(expanded)
            if path.exists():
                candidates.append(path)
        return candidates

    def _pick_best_candidate(self, candidates: Iterable[Path]) -> Optional[str]:
        """Выбирает лучший путь Steam по набору признаков."""
        best_path = None
        best_score = -1
        seen = set()
        for path in candidates:
            if not path:
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            score = self._score_candidate(path)
            if score > best_score:
                best_score = score
                best_path = path
        if best_path and best_score >= 1:
            return str(best_path)
        return None

    def _score_candidate(self, path: Path) -> int:
        """Оценивает кандидат пути Steam."""
        if not path.exists():
            return -1
        score = 0
        log_path = path / self.log_rel_path
        if log_path.exists():
            score += 3
        if (path / "steamapps").exists():
            score += 2
        if (path / "config" / "config.vdf").exists():
            score += 1
        if (path / "logs").exists():
            score += 1
        return score


def split_registry_path(value: str) -> Tuple[Optional[str], Optional[str]]:
    """Разбивает путь реестра на корень и подпуть."""
    if not value:
        return None, None
    parts = value.split("\\", 1)
    root_name = parts[0].strip() if parts[0].strip() else None
    subkey = parts[1] if len(parts) > 1 else ""
    return root_name, subkey


class ContentLogReader:
    """Читает хвост content_log.txt."""
    def __init__(self, log_path: Path, tail_bytes: int, encoding: str):
        """Инициализирует параметры чтения лога."""
        self.log_path = log_path
        self.tail_bytes = tail_bytes
        self.encoding = encoding

    def read(self) -> str:
        """Возвращает текст из конца файла лога."""
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                start = max(0, size - self.tail_bytes)
                fh.seek(start)
                data = fh.read()
            return data.decode(self.encoding, errors="ignore")
        except OSError:
            return ""


class ContentLogParser:
    """Парсит статус и скорость из content_log.txt."""
    def __init__(self):
        """Инициализирует регулярные выражения парсинга."""
        self.rate_re = re.compile(r"Current download rate:\s*([0-9.]+)\s*([A-Za-z/]+)", re.IGNORECASE)
        self.appid_re = re.compile(r"AppID\s+(\d+)\s+update started", re.IGNORECASE)
        self.suspended_re = re.compile(r"\bSuspended\b", re.IGNORECASE)
        self.canceled_re = re.compile(r"update canceled|update cancelled|update stopped", re.IGNORECASE)
        self.resumed_re = re.compile(r"update resumed|update started", re.IGNORECASE)

    def parse(self, text: str, previous: Optional[DownloadSnapshot]) -> DownloadSnapshot:
        """Парсит текст лога и возвращает снимок состояния."""
        lines = text.splitlines()
        active_appid = None
        start_idx = 0
        for idx in range(len(lines) - 1, -1, -1):
            match = self.appid_re.search(lines[idx])
            if match:
                active_appid = match.group(1)
                start_idx = idx
                break

        pause_idx = None
        resume_idx = None
        rate_idx = None
        rate_value = None
        rate = None

        token = f"AppID {active_appid}" if active_appid else None
        for idx in range(start_idx, len(lines)):
            line = lines[idx]
            if token and token in line:
                if self.suspended_re.search(line) or self.canceled_re.search(line):
                    pause_idx = idx
                elif self.resumed_re.search(line):
                    resume_idx = idx
            match = self.rate_re.search(line)
            if match:
                rate_idx = idx
                rate_value = self._to_float(match.group(1))
                rate = f"{match.group(1)} {match.group(2)}"

        if pause_idx is not None and (resume_idx is None or pause_idx >= resume_idx):
            status = "paused"
        elif resume_idx is not None:
            status = "downloading"
        elif rate_value is not None:
            status = "paused" if rate_value <= 0 else "downloading"
        else:
            status = "unknown"

        if status == "paused":
            rate = "0 Mbps"
        elif status == "downloading":
            if rate_idx is None or (resume_idx is not None and rate_idx < resume_idx) or (pause_idx is not None and rate_idx <= pause_idx):
                rate = None

        if previous:
            if active_appid is None:
                active_appid = previous.appid
            if status == "unknown":
                status = previous.status
            if rate is None and status == "downloading" and previous.status == "downloading" and previous.appid == active_appid:
                rate = previous.rate

        return DownloadSnapshot(appid=active_appid, rate=rate, status=status)

    def _to_float(self, value: str) -> Optional[float]:
        """Преобразует строку в float, возвращая None при ошибке."""
        try:
            return float(value)
        except ValueError:
            return None


class AppManifestStore:
    """Читает appmanifest_*.acf из библиотек Steam."""
    def __init__(self, parser: VdfKeyValueParser, manifest_pattern: str):
        """Инициализирует парсер и шаблон манифестов."""
        self.parser = parser
        self.manifest_pattern = manifest_pattern
        self.appid_re = re.compile(r"appmanifest_(\d+)\.acf", re.IGNORECASE)
        self.glob_pattern = self._to_glob_pattern(manifest_pattern)

    def list_manifests(self, library_paths: Iterable[Path]) -> Tuple[AppManifest, ...]:
        """Возвращает список манифестов из библиотек Steam."""
        manifests = []
        for library in library_paths:
            try:
                root = Path(library)
                if not root.exists():
                    continue
                for path in root.glob(self.glob_pattern):
                    manifest = self._read_manifest(path)
                    if manifest:
                        manifests.append(manifest)
            except OSError:
                continue
        return tuple(manifests)

    def _read_manifest(self, path: Path) -> Optional[AppManifest]:
        """Читает и парсит конкретный appmanifest."""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        data = self.parser.parse(text)
        appid = data.get("appid") or self._appid_from_filename(path.name)
        if not appid:
            return None
        name = data.get("name") or f"AppID {appid}"
        bytes_downloaded = self._to_int(data.get("BytesDownloaded"))
        bytes_to_download = self._to_int(data.get("BytesToDownload"))
        state_flags = self._to_int(data.get("StateFlags"))
        return AppManifest(
            appid=str(appid),
            name=name,
            bytes_downloaded=bytes_downloaded,
            bytes_to_download=bytes_to_download,
            state_flags=state_flags,
        )

    def _appid_from_filename(self, filename: str) -> Optional[str]:
        """Извлекает AppID из имени файла манифеста."""
        match = self.appid_re.search(filename)
        return match.group(1) if match else None

    def _to_glob_pattern(self, pattern: str) -> str:
        """Преобразует шаблон с {appid} в glob-шаблон."""
        try:
            return pattern.format(appid="*")
        except (KeyError, ValueError):
            return pattern

    def _to_int(self, value: Optional[str]) -> int:
        """Преобразует значение в int, возвращая 0 при ошибке."""
        try:
            return int(value) if value is not None else 0
        except ValueError:
            return 0


class SteamLibrary:
    """Возвращает список библиотек Steam."""
    def __init__(self, steam_root: Path, library_vdf_path: Path):
        """Инициализирует пути Steam и libraryfolders.vdf."""
        self.steam_root = steam_root
        self.library_vdf_path = library_vdf_path
        self.path_re = re.compile(r'"path"\s+"([^"]+)"')

    def list_paths(self) -> Tuple[Path, ...]:
        """Возвращает уникальные пути библиотек Steam."""
        libraries = [self.steam_root]
        try:
            if self.library_vdf_path.exists():
                text = self.library_vdf_path.read_text(encoding="utf-8", errors="ignore")
                for match in self.path_re.finditer(text):
                    raw = match.group(1)
                    path_str = raw.replace("\\\\", "\\")
                    libraries.append(Path(path_str))
        except OSError:
            pass
        unique = []
        seen = set()
        for path in libraries:
            norm = str(path).lower()
            if norm in seen:
                continue
            seen.add(norm)
            unique.append(path)
        return tuple(unique)


