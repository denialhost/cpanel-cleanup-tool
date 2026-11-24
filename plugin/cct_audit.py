"""Monthly per-account audit log of deletions. Stored under ~/.cct/audit/."""
import json
import os
import re
import stat
import time
from datetime import datetime, timezone

from cct_core import CleanupError, open_parent, validate_relative

AUDIT_ROOT = ".cct/audit"
MONTH = re.compile(r"\d{4}-\d{2}")


def _month_name(when=None):
    moment = datetime.fromtimestamp(when or time.time(), tz=timezone.utc)
    return moment.strftime("%Y-%m")


def _owned_dir(fd, account, private=False):
    info = os.fstat(fd)
    if info.st_uid != account.uid or not stat.S_ISDIR(info.st_mode):
        raise CleanupError("El directorio de auditoría no pertenece a la cuenta.")
    if private and info.st_mode & 0o077:
        raise CleanupError("El directorio de auditoría debe ser privado (0700).")


def _open_or_mkdir(parent_fd, name, account):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        try:
            child = os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            if os.geteuid() == 0:
                os.chown(name, account.uid, -1, dir_fd=parent_fd, follow_symlinks=False)
            child = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise CleanupError("El directorio de auditoría no es válido o es un enlace simbólico.") from exc
    try:
        _owned_dir(child, account, private=True)
    except CleanupError:
        os.close(child)
        raise
    return child


def _ensure_audit_dir(account):
    """Return a directory fd for ~/.cct/audit. Caller must close it."""
    if os.name != "posix":
        raise CleanupError("La auditoría requiere Linux.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        home_fd = os.open(str(account.home), flags)
    except OSError as exc:
        raise CleanupError("No se pudo abrir el home de la cuenta para auditoría.") from exc
    try:
        _owned_dir(home_fd, account, private=False)
        cct_fd = _open_or_mkdir(home_fd, ".cct", account)
        try:
            return _open_or_mkdir(cct_fd, "audit", account)
        finally:
            os.close(cct_fd)
    finally:
        os.close(home_fd)


def audit_path(month=None):
    name = month or _month_name()
    if not MONTH.fullmatch(name):
        raise CleanupError("Mes de auditoría inválido.")
    return f"{AUDIT_ROOT}/{name}.log"


def append_events(account, events, actor, via):
    """Append JSONL events. Failures raise CleanupError; callers may warn."""
    if not isinstance(events, list) or not events:
        return {"wrote": 0, "path": audit_path()}
    if not isinstance(actor, str) or not actor or len(actor) > 128:
        raise CleanupError("Actor de auditoría inválido.")
    if via not in {"cpanel", "whm"}:
        raise CleanupError("Origen de auditoría inválido.")
    relative = audit_path()
    validate_relative(relative)
    lines = []
    now = int(time.time())
    for event in events:
        if not isinstance(event, dict):
            raise CleanupError("Evento de auditoría inválido.")
        row = {
            "ts": int(event.get("ts") or now),
            "actor": actor,
            "via": via,
            "account": account.username,
            "category": event.get("category") or "",
            "relative_path": event.get("relative_path") or "",
            "bytes": int(event.get("bytes") or 0),
            "id": event.get("id") or "",
            "result": event.get("result") or "deleted",
        }
        if event.get("error"):
            row["error"] = str(event["error"])[:500]
        lines.append(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
    payload = "".join(lines).encode("utf-8")
    directory = _ensure_audit_dir(account)
    try:
        name = relative.rsplit("/", 1)[-1]
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(name, flags, 0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != account.uid:
                raise CleanupError("El archivo de auditoría no es válido.")
            os.write(fd, payload)
        finally:
            os.close(fd)
        if os.geteuid() == 0:
            os.chown(name, account.uid, -1, dir_fd=directory, follow_symlinks=False)
            os.chmod(name, 0o600, dir_fd=directory, follow_symlinks=False)
    except (OSError, CleanupError) as exc:
        raise CleanupError(f"No se pudo escribir la auditoría: {exc}") from exc
    finally:
        os.close(directory)
    return {"wrote": len(lines), "path": relative}


def list_months(account):
    months = []
    try:
        with open_parent(account, AUDIT_ROOT + "/.cct-anchor") as (fd, _):
            with os.scandir(fd) as entries:
                for entry in entries:
                    name = entry.name
                    if name.endswith(".log") and MONTH.fullmatch(name[:-4]) and entry.is_file(follow_symlinks=False):
                        months.append(name[:-4])
    except FileNotFoundError:
        return []
    months.sort(reverse=True)
    return months


def read_month(account, month=None, limit=500):
    if not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise CleanupError("Límite de auditoría inválido.")
    relative = audit_path(month)
    events = []
    try:
        with open_parent(account, relative) as (fd, name):
            opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(opened, "r", encoding="utf-8", errors="replace") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != account.uid:
                    raise CleanupError("Archivo de auditoría inválido.")
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        events.append(row)
                    if len(events) >= limit:
                        break
    except FileNotFoundError:
        return {"month": month or _month_name(), "events": [], "path": relative}
    return {"month": month or _month_name(), "events": events, "path": relative}
