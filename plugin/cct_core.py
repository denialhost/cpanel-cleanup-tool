"""Account-scoped cPanel cleanup. Python 3.9+, standard library only."""
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

VERSION = "0.4.4"
DAY = 86400
MAX_ITEMS = 5000
MAX_VISITED = 150000
SCAN_SECONDS = 20
MAX_DELETE = 250
EXCLUDED = {".ssh", ".gnupg", ".cpanel", ".cct", ".cagefs", ".git", "node_modules", "vendor", "etc", "ssl", "mail", ".local"}
BACKUP_DIRS = {"backups", "backup", "updraft", "ai1wm-backups", "wpvividbackups", "backupbuddy_backups"}
ARCHIVE = re.compile(r"\.(?:tar(?:\.gz|\.bz2|\.xz)?|tgz|zip|sql(?:\.gz)?|wpress)$", re.I)
ROTATED = re.compile(r"^(?:error_log|access_log|error-log|access-log|.+\.log)(?:(?:[.-]\d[\d._-]*)?\.(?:gz|bz2|xz)|[.-]\d[\d._-]*)$", re.I)
# Direct IMAP short names only (after stripping a leading INBOX.). Nested custom
# folders keep matching by their final segment; GUID resolution finds the path.
MAILBOXES = {
    "spam": "spam", "junk": "spam", "bulk": "spam",
    "junk e-mail": "spam", "junk-email": "spam", "junkemail": "spam",
    "correo no deseado": "spam", "correo-no-deseado": "spam", "correobasura": "spam",
    "trash": "mail_trash",
}


class CleanupError(Exception):
    pass


@dataclass(frozen=True)
class Account:
    username: str
    home: Path
    uid: int

    @classmethod
    def _from_pwd(cls, entry):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", entry.pw_name):
            raise CleanupError("No se pudo identificar una cuenta de cPanel válida.")
        home = Path(entry.pw_dir).resolve(strict=True)
        if home == Path("/") or home.stat().st_uid != entry.pw_uid:
            raise CleanupError("El directorio de la cuenta no es válido.")
        return cls(entry.pw_name, home, entry.pw_uid)

    @classmethod
    def current(cls):
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() == 0:
            raise CleanupError("El plugin debe ejecutarse con el usuario de cPanel, sin privilegios root.")
        import pwd
        entry = pwd.getpwuid(os.geteuid())
        remote = os.environ.get("REMOTE_USER")
        if remote and remote != entry.pw_name:
            raise CleanupError("La sesión y el usuario del proceso no coinciden.")
        return cls._from_pwd(entry)

    @classmethod
    def from_username(cls, username):
        """WHM root may target a cPanel account; never used by the client plugin."""
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise CleanupError("Solo WHM root puede operar sobre otra cuenta.")
        if not isinstance(username, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", username):
            raise CleanupError("Cuenta de cPanel no válida.")
        import pwd
        try:
            entry = pwd.getpwnam(username)
        except KeyError as exc:
            raise CleanupError("La cuenta indicada no existe en este servidor.") from exc
        return cls._from_pwd(entry)

    @classmethod
    def resolve(cls):
        target = os.environ.get("CCT_TARGET_USER")
        if target:
            return cls.from_username(target)
        return cls.current()


class UAPI:
    def __init__(self, account, transport=None):
        self.account = account
        self.transport = transport

    def call(self, module, function, **params):
        if self.transport is None:
            raise CleanupError("Abre el plugin desde cPanel para usar su conexión LiveAPI.")
        try:
            document = self.transport.call("uapi", module, function, params)
            if not isinstance(document, dict):
                raise ValueError("Invalid API document")
            response = document.get("cpanelresult", document)
            response = response.get("result", response)
            if not isinstance(response, dict):
                raise ValueError("Invalid API result")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise CleanupError(f"No se pudo consultar cPanel ({module}/{function}).") from exc
        if response.get("status") != 1:
            messages = response.get("errors") or response.get("messages") or ["Operación no disponible."]
            raise CleanupError(f"{module}/{function}: " + "; ".join(map(str, messages)))
        return response.get("data")

    def require_feature(self):
        self.call("Features", "has_feature", name="cct")

    def _disk_usage_api2(self, function):
        if self.transport is None:
            raise CleanupError("LiveAPI is not connected")
        document = self.transport.call("api2", "DiskUsage", function, {})
        response = document.get("cpanelresult", document)
        data = response.get("data") or []
        if response.get("event", {}).get("result") != 1 or not isinstance(data, list) or not data:
            raise ValueError("DiskUsage unavailable")
        row = data[0] if isinstance(data[0], dict) else {}
        flag = row.get("status")
        return flag, str(row.get("statusmsg") or "")

    def clear_disk_cache(self):
        """Invalidate only. Prefer refresh_disk_usage() after deletions."""
        return self.refresh_disk_usage(rebuild=False)

    def refresh_disk_usage(self, rebuild=True, poll=False):
        """Clear and optionally rebuild Disk Usage cache via API 2.

        clearcache invalidates; buildcache regenerates. There is no UAPI
        equivalent. poll=True retries buildcache without clearing again.
        """
        sources = []
        try:
            if not poll:
                flag, _ = self._disk_usage_api2("clearcache")
                sources.append("DiskUsage/clearcache")
                if flag == 0 and not rebuild:
                    status = "pending"
                elif flag not in {0, 1}:
                    raise ValueError("Unexpected clearcache status")
                elif not rebuild:
                    status = "cleared"
                else:
                    status = None
            else:
                status = None
            if rebuild or poll:
                flag, message = self._disk_usage_api2("buildcache")
                sources.append("DiskUsage/buildcache")
                if flag == 1:
                    status = "built"
                elif flag == 0:
                    status = "building"
                else:
                    raise ValueError(message or "Unexpected buildcache status")
            if status is None:
                status = "cleared"
        except (CleanupError, OSError, ValueError, TypeError, KeyError, AttributeError):
            status = "unavailable"
        messages = {
            "built": "Se regeneró la caché de uso de disco de tu cuenta. Cuota y desglose se actualizaron con los datos disponibles ahora.",
            "building": "cPanel está regenerando la caché de uso de disco. Reintentamos automáticamente en unos segundos.",
            "cleared": "Se vació la caché de uso de disco de tu cuenta. cPanel puede tardar en recalcular sus indicadores.",
            "pending": "cPanel indicó que el vaciado de caché está en curso. Sus indicadores pueden tardar en actualizarse.",
            "unavailable": "No se pudo regenerar la caché de cPanel. El uso mostrado puede no reflejar todavía los archivos eliminados.",
        }
        retry = 3 if status == "building" else 30 if status in {"cleared", "pending"} else 0
        return {"status": status, "message": messages[status], "retry_after_seconds": retry,
                "retry_is_guaranteed": status == "building", "source": "+".join(sources) or "DiskUsage"}


def _nonnegative(value):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("Invalid quota")
    return number


def normalize_quota(data):
    """get_quota_info uses MiB, despite naming the unit MB in UAPI."""
    try:
        used = int(_nonnegative(data["megabytes_used"]) * 1048576)
        limit = int(_nonnegative(data["megabyte_limit"]) * 1048576)
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise CleanupError("cPanel devolvió una cuota desconocida; no se calcularán porcentajes.") from exc
    return {"used_bytes": used, "limit_bytes": limit or None,
            "free_bytes": max(0, limit - used) if limit else None,
            "inodes_used": data.get("inodes_used"), "inode_limit": data.get("inode_limit"),
            "source": "Quota/get_quota_info", "checked_at": int(time.time()),
            "note": "La cuota puede actualizarse con retraso. Un límite cero significa sin límite o cuotas desactivadas."}


def get_quota(api):
    try:
        return normalize_quota(api.call("Quota", "get_quota_info"))
    except CleanupError as exc:
        return {"used_bytes": None, "limit_bytes": None, "free_bytes": None, "error": str(exc)}


def list_whm_accounts(api):
    """WHM root listing. Values come from WHM API1 listaccts."""
    if api.transport is None:
        raise CleanupError("LiveAPI is not connected")
    document = api.transport.call("whm", "listaccts", "list", {})
    if not isinstance(document, dict):
        raise CleanupError("WHM no devolvió el listado de cuentas.")
    data = document.get("data") or {}
    rows = data.get("acct") if isinstance(data, dict) else None
    if rows is None and isinstance(document.get("acct"), list):
        rows = document["acct"]
    if not isinstance(rows, list):
        raise CleanupError("El listado de cuentas de WHM no es válido.")
    accounts = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        user = row.get("user") or row.get("username")
        if not isinstance(user, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", user):
            continue
        accounts.append({
            "user": user,
            "domain": row.get("domain") or "",
            "plan": row.get("plan") or row.get("package") or "",
            "diskused": row.get("diskused") or row.get("disk_usage") or "",
            "disklimit": row.get("disklimit") or row.get("disk_limit") or "",
            "email": row.get("email") or "",
        })
    accounts.sort(key=lambda item: item["user"])
    return accounts


def measure_mail_usage(account):
    """Count physical blocks once per inode; never read message contents."""
    try:
        # The inherited directory descriptor anchors du to this account's mail/.
        # -H follows only that descriptor argument, never symlinks within mail/.
        with open_parent(account, "mail/.cct-usage") as (mail_fd, _):
            result = subprocess.run(
                ["/usr/bin/du", "--summarize", "--block-size=1", "--one-file-system",
                 "--dereference-args", "--", f"/proc/self/fd/{mail_fd}"],
                pass_fds=(mail_fd,), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8, check=True)
        parts = result.stdout.split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            raise ValueError("Physical mail usage unavailable")
        return int(parts[0])
    except FileNotFoundError:
        # A missing mail directory is empty, but a missing utility is unavailable.
        if not (account.home / "mail").exists():
            return 0
        raise


def normalize_storage(data, physical_mail_bytes):
    """Replace cPanel's virtual mail counters with physical allocated blocks."""
    def size(value):
        number = _nonnegative(value)
        if number != number.to_integral_value():
            raise ValueError("Invalid byte counter")
        return int(number)

    home = data["homedir"]
    children = home["contents"]
    if not isinstance(children, list):
        raise ValueError("Directory summary unavailable")
    counters = {"mail": size(physical_mail_bytes) + size(data["mailman"]),
                "web": 0, "databases": size(data["mysql"]) + size(data["pgsql"]),
                "backups": 0, "logs": 0, "other": 0}
    names = set()
    virtual_mail_bytes = 0
    for child in children:
        name = child["name"]
        if not isinstance(name, str) or name in names:
            raise ValueError("Invalid directory summary")
        names.add(name)
        if name == "mail":
            virtual_mail_bytes = size(child["user_contained_usage"])
        category = ("web" if name in {"public_html", "public_ftp"} else
                    "backups" if name in BACKUP_DIRS else "logs" if name == "logs" else None)
        if category:
            counters[category] += size(child["user_contained_usage"])
    # cPanel injects logical mailbox counters into both mail/ and home totals.
    # Remove that virtual subtree before combining it with physical mail blocks.
    non_mail_bytes = size(home["user_contained_usage"]) - virtual_mail_bytes
    if non_mail_bytes < 0:
        raise ValueError("Inconsistent directory counters")
    counters["other"] = max(0, non_mail_bytes - counters["web"] - counters["backups"] - counters["logs"])
    return {"status": "ok", "categories": [{"key": key, "bytes": value} for key, value in counters.items()],
            "total_bytes": sum(counters.values()), "source": "DiskUsage/fetchdiskusagewithextras",
            "basis": "allocated_bytes",
            "checked_at": int(time.time())}


def get_storage(api):
    try:
        if api.transport is None:
            raise CleanupError("LiveAPI is not connected")
        document = api.transport.call("api2", "DiskUsage", "fetchdiskusagewithextras", {})
        response = document.get("cpanelresult", document)
        rows = response.get("data")
        if response.get("event", {}).get("result") != 1 or not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("Storage summary unavailable")
        return normalize_storage(rows[0], measure_mail_usage(api.account))
    except (CleanupError, OSError, ValueError, TypeError, KeyError, AttributeError, InvalidOperation, subprocess.SubprocessError):
        return {"status": "unavailable", "categories": [],
                "message": "El desglose no está disponible por ahora. Puedes seguir revisando y limpiando tu cuenta."}


def fingerprint(st):
    # Browser JSON numbers cannot preserve nanoseconds or large inode numbers.
    # Keep every component exact across Python -> JavaScript -> Python.
    return [str(value) for value in (st.st_dev, st.st_ino, st.st_size,
                                    st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)]


def allocated(st):
    return int(st.st_blocks * 512) if hasattr(st, "st_blocks") else st.st_size


def validate_relative(relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise CleanupError("Ruta inválida.")
    parts = relative.split("/")
    if any(p in {"", ".", ".."} for p in parts) or PurePosixPath(relative).is_absolute():
        raise CleanupError("La ruta debe estar dentro de la cuenta.")
    return parts


@contextmanager
def open_parent(account, relative):
    """Anchor traversal to directory descriptors; never follow a symlink."""
    parts = validate_relative(relative)
    if os.name != "posix":
        raise CleanupError("El borrado real requiere Linux.")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(str(account.home), flags)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, parts[-1]
    finally:
        os.close(fd)


def safe_stat(account, relative):
    with open_parent(account, relative) as (fd, name):
        return os.stat(name, dir_fd=fd, follow_symlinks=False)


def mailbox_short_name(folder):
    if not isinstance(folder, str) or not folder:
        return ""
    return folder[6:] if folder.startswith("INBOX.") else folder


def mailbox_category(folder):
    return MAILBOXES.get(mailbox_short_name(folder).lower())


def read_maildir_guid(account, relative):
    with open_parent(account, relative + "/dovecot-uidlist") as (fd, name):
        opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(opened, "r", encoding="utf-8", errors="replace") as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != account.uid:
                raise CleanupError("Índice de correo inválido.")
            header = stream.readline(4096).strip().split()
            if not header or header[0] != "3":
                raise CleanupError("Solo se admite el formato Maildir v3.")
            guid = next((part[1:] for part in header if part.startswith("G")), "")
            return guid.lower() if re.fullmatch(r"[a-fA-F0-9]{32}", guid) else None


def maildir_paths_by_guid(account, base, on_visit=None):
    """Map Maildir v3 GUID -> relative path under the account mail tree."""
    paths = [base]
    stop = False
    try:
        with open_parent(account, base + "/.cct-anchor") as (fd, _):
            with os.scandir(fd) as entries:
                for entry in entries:
                    if on_visit and on_visit():
                        stop = True
                        break
                    if entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                        paths.append(base + "/" + entry.name)
    except FileNotFoundError:
        # No mail tree yet: callers fall back to the classic .Folder path.
        return {}
    matches = {}
    for relative in paths:
        if stop or (on_visit and on_visit()):
            break
        try:
            guid = read_maildir_guid(account, relative)
            if guid:
                # Ambiguous aliases must never identify a deletion target.
                matches[guid] = relative if guid not in matches else None
        except (OSError, CleanupError):
            continue
    return matches


def mail_account_base(account_username, address):
    if address == account_username:
        return "mail"
    if re.fullmatch(r"[A-Za-z0-9_+.-]+@[A-Za-z0-9.-]+", address):
        local, domain = address.rsplit("@", 1)
        if local not in {".", ".."} and domain not in {".", ".."}:
            return f"mail/{domain}/{local}"
    return None


def classify(relative, st, now):
    parts = PurePosixPath(relative).parts
    name = parts[-1]
    # Never classify mail storage or account configuration as ordinary files.
    if any(p in EXCLUDED for p in parts[:-1]):
        return None
    age = max(0, now - st.st_mtime) / DAY
    if parts[0] == ".trash":
        return "trash", "review", "Archivo en la papelera del administrador de archivos."
    if ARCHIVE.search(name) and (any(p.lower() in BACKUP_DIRS for p in parts[:-1]) or (len(parts) == 1 and name.startswith("backup-"))):
        return "backups", "review", "Respaldo: confirma que tienes una copia vigente antes de eliminarlo."
    if ROTATED.search(name) or (parts[0] == "logs" and name.lower().endswith((".gz", ".bz2", ".xz"))):
        if age >= 7:
            return "logs", "candidate", "Registro rotado sin modificaciones durante al menos 7 días."
        return "logs", "protected", "Registro reciente; se conserva durante al menos 7 días."
    if name.lower() in {"error_log", "access_log", "error-log", "access-log"} or name.lower().endswith(".log"):
        return "logs", "protected", "Puede ser un registro activo. Requiere rotación antes de limpiarlo."
    return None


def make_item(owner, relative, st, category, risk, reason, **extra):
    key = relative + "\0" + str(extra.get("uid", ""))
    recoverable = allocated(st) if st.st_nlink == 1 else 0
    if st.st_nlink != 1:
        risk, reason = "protected", "Tiene enlaces duros: no se puede atribuir un ahorro a este archivo."
    if st.st_uid != owner.uid:
        risk, reason = "protected", "El archivo pertenece a otro usuario."
    return {"id": hashlib.sha256(key.encode('utf-8', 'surrogateescape')).hexdigest()[:24], "name": PurePosixPath(relative).name,
            "relative_path": relative, "path": str(owner.home / relative), "category": category,
            "size_bytes": st.st_size, "reclaimable_bytes": recoverable, "modified_at": st.st_mtime,
            "fingerprint": fingerprint(st), "risk": risk, "selectable": risk != "protected",
            "reason": reason, **extra}


class Scanner:
    def __init__(self, account, api, max_items=MAX_ITEMS, seconds=SCAN_SECONDS):
        self.account, self.api = account, api
        self.max_items, self.seconds = max_items, seconds
        self.items, self.warnings = [], []
        self.partial = False
        self.visited = 0
        self.deadline = time.monotonic() + seconds
        self.now = time.time()
        self.mail_cutoff = None
        self.mailbox_name = None

    def limited(self):
        if len(self.items) >= self.max_items or self.visited >= MAX_VISITED or time.monotonic() >= self.deadline:
            self.partial = True
            return True
        return False

    def files(self):
        # POSIX fwalk keeps ancestor directory handles anchored during enumeration.
        # Windows is used only by local scan tests, never for production deletion.
        if os.name == "posix":
            iterator = os.fwalk(self.account.home, follow_symlinks=False, onerror=self.walk_error)
        else:
            iterator = ((*row, None) for row in os.walk(self.account.home, followlinks=False, onerror=self.walk_error))
        for root, dirs, names, fd in iterator:
            if self.limited():
                break
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (Path(root) / d).is_symlink())
            for name in names:
                if self.limited():
                    break
                self.visited += 1
                path = Path(root) / name
                relative = path.relative_to(self.account.home).as_posix()
                try:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False) if fd is not None else path.lstat()
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    match = classify(relative, st, self.now)
                    if match:
                        self.items.append(make_item(self.account, relative, st, *match))
                except OSError:
                    self.walk_error(None)
        backups = defaultdict(list)
        for item in self.items:
            if item["category"] == "backups":
                backups[str(PurePosixPath(item["relative_path"]).parent)].append(item)
        for group in backups.values():
            newest = max(group, key=lambda i: (i["modified_at"], i["relative_path"]))
            # Backup sets often span several archives. Retain a 48-hour window
            # around the latest member rather than retaining only one file.
            stamp = re.search(r"20\d{2}[-_]\d{2}[-_]\d{2}", newest["name"])
            for item in group:
                if item["modified_at"] >= newest["modified_at"] - 2 * DAY or (stamp and stamp.group() in item["name"]):
                    item.update(risk="protected", selectable=False, reason="Se conservan los respaldos del conjunto más reciente (ventana de 48 horas).")
        # A partial listing cannot establish which backup is newest.
        if self.partial:
            for group in backups.values():
                for item in group:
                    item.update(risk="protected", selectable=False, reason="Análisis incompleto: no se pudo comprobar la retención de respaldos.")

    def walk_error(self, error):
        message = "Algunas rutas no pudieron leerse; el análisis no representa la cuenta completa."
        if message not in self.warnings:
            self.warnings.append(message)
        self.partial = True

    def mail(self):
        if self.limited():
            return
        try:
            accounts = self.api.call("Email", "list_pops") or []
        except CleanupError as exc:
            self.warnings.append(str(exc))
            self.partial = True
            return
        # Include the default cPanel mailbox as well as domain mail accounts.
        addresses = [self.account.username] + [a.get("email", "") for a in accounts]
        for address in dict.fromkeys(addresses):
            if self.limited():
                break
            base = mail_account_base(self.account.username, address)
            if not base:
                continue
            try:
                statuses = self.api.call("Mailboxes", "get_mailbox_status_list", account=address) or []
                def bump():
                    self.visited += 1
                    return self.limited()
                paths = maildir_paths_by_guid(self.account, base, on_visit=bump)
                if self.limited():
                    break
                for mailbox in statuses:
                    folder = mailbox.get("mailbox", "")
                    category = mailbox_category(folder)
                    guid = str(mailbox.get("guid", "")).lower()
                    short = mailbox_short_name(folder)
                    if not category or int(mailbox.get("messages", 0)) <= 0 or not re.fullmatch(r"[a-fA-F0-9]{32}", guid):
                        continue
                    relative = paths.get(guid) or f"{base}/.{short}"
                    self.mailbox_name = folder
                    self.maildir(address, relative, guid, category)
            except (CleanupError, OSError, ValueError) as exc:
                self.partial = True
                self.warnings.append(f"No se pudo inspeccionar el correo de {address}: {exc}")

    def maildir(self, address, relative, guid, category):
        # Only Maildir v3 is supported. Missing/other formats fail closed.
        # No message content is read; UIDs map filesystem candidates to Dovecot.
        with open_parent(self.account, relative + "/dovecot-uidlist") as (fd, name):
            uid_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(uid_fd, "r", encoding="utf-8", errors="replace") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise CleanupError("Índice de correo inválido.")
                header = stream.readline(4096).strip().split()
                if not header or header[0] != "3":
                    raise CleanupError("Formato distinto de Maildir v3; revisa este buzón en Uso del disco de correo.")
                file_guid = next((x[1:] for x in header if x.startswith("G")), "")
                validity = next((x[1:] for x in header if x.startswith("V")), "")
                if file_guid.lower() != guid.lower() or not validity.isdigit():
                    raise CleanupError("El índice y la carpeta de correo no coinciden.")
                while not self.limited():
                    line = stream.readline(65536)
                    if not line:
                        break
                    self.visited += 1
                    if len(line) >= 65536:
                        raise CleanupError("Entrada de índice demasiado larga.")
                    prefix, sep, stored_name = line.rstrip("\n").partition(":")
                    uid_text = prefix.split(" ", 1)[0]
                    if not sep or not uid_text.isdigit() or not (0 < int(uid_text) < 2**32):
                        continue
                    # uidlist omits the variable Maildir flag suffix ':2,...'.
                    base_name = stored_name.split(":2,", 1)[0]
                    if not base_name or "/" in base_name or "\\" in base_name:
                        continue
                    self._mail_message(address, relative, fd, base_name, int(uid_text), guid, validity, category)

    def _mail_message(self, address, relative, parent_fd, base_name, uid, guid, validity, category):
        for subdir in ("cur", "new"):
            try:
                child = os.open(subdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            except FileNotFoundError:
                continue
            try:
                # A uidlist basename can have changed flags. Exact matches first;
                # enumerate this folder once per mailbox via a cached basename map.
                cache_key = (relative, subdir)
                if not hasattr(self, "_mail_names"):
                    self._mail_names = {}
                if cache_key not in self._mail_names:
                    mapping = {}
                    with os.scandir(child) as entries:
                        for entry in entries:
                            self.visited += 1
                            if self.limited():
                                break
                            key = entry.name.split(":2,", 1)[0]
                            mapping[key] = entry.name if key not in mapping else None
                    self._mail_names[cache_key] = mapping
                filename = self._mail_names[cache_key].get(base_name)
                if not filename:
                    continue
                st = os.stat(filename, dir_fd=child, follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode):
                    continue
                if self.mail_cutoff is not None and st.st_mtime >= self.mail_cutoff:
                    return
                rel = f"{relative}/{subdir}/{filename}"
                self.items.append(make_item(self.account, rel, st, category, "review",
                    "Mensaje anterior a la fecha elegida." if category == "mail_date" else "Mensaje en spam o papelera. Se elimina por su UID mediante cPanel, sin leer el contenido.",
                    account=address, mailbox_guid=guid, uid=uid, uid_validity=validity, mailbox=self.mailbox_name))
                return
            finally:
                os.close(child)

    def scan(self):
        self.files()
        if os.name == "posix":
            self.mail()
        # If mail exhausted the shared budget, backup safety remains conservative.
        if self.partial:
            for item in self.items:
                if item["category"] == "backups":
                    item.update(risk="protected", selectable=False, reason="Análisis incompleto: respaldos protegidos.")
        if self.partial:
            self.warnings.append("Resultados parciales: se alcanzó un límite o hubo rutas/servicios inaccesibles. No se ha analizado todo el espacio.")
        unique = {item["id"]: item for item in self.items}
        return {"items": sorted(unique.values(), key=lambda i: i["reclaimable_bytes"], reverse=True),
                "warnings": list(dict.fromkeys(self.warnings)), "partial": self.partial,
                "scanned_at": self.now, "visited": self.visited,
                "account": {"username": self.account.username, "home": str(self.account.home)}, "version": VERSION}


def same_item(requested, current):
    keys = ("id", "relative_path", "fingerprint", "account", "mailbox_guid", "uid", "uid_validity")
    return all(requested.get(k) == current.get(k) for k in keys)


def unlink_candidate(account, item):
    relative = item["relative_path"]
    with open_parent(account, relative) as (fd, name):
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != account.uid:
            raise CleanupError("El archivo no pertenece a la cuenta analizada.")
        if st.st_nlink != 1 or fingerprint(st) != item["fingerprint"]:
            raise CleanupError("El archivo cambió después del análisis. Vuelve a analizar.")
        match = classify(relative, st, time.time())
        if not match or match[1] == "protected":
            raise CleanupError("El archivo ya no es candidato a limpieza.")
        os.unlink(name, dir_fd=fd)


def delete_selected(account, api, requested, confirm, backups_ack=False, scanner_factory=Scanner,
                    mail_cutoff=None, actor=None, via="cpanel"):
    if confirm != "ELIMINAR":
        raise CleanupError("Escribe ELIMINAR para confirmar el borrado permanente.")
    if not isinstance(requested, list) or not 1 <= len(requested) <= MAX_DELETE:
        raise CleanupError(f"Selecciona entre 1 y {MAX_DELETE} elementos por operación.")
    if not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in requested):
        raise CleanupError("Selección inválida.")
    if len({i["id"] for i in requested}) != len(requested):
        raise CleanupError("La selección contiene elementos duplicados.")
    actor = actor or ("root" if via == "whm" else f"cpanel:{account.username}")
    before = get_quota(api)
    inventory = scanner_factory(account, api).scan()
    current = {i["id"]: i for i in inventory["items"]}
    deleted, failed, mail_groups = [], [], defaultdict(list)
    for req in requested:
        item = current.get(req["id"])
        try:
            if not item or not same_item(req, item):
                raise CleanupError("El elemento cambió o ya no está disponible. Vuelve a analizar.")
            if not item["selectable"]:
                raise CleanupError(item["reason"])
            if item["category"] == "backups" and backups_ack is not True:
                raise CleanupError("Debes confirmar que revisaste los respaldos seleccionados.")
            if item["category"] in {"spam", "mail_trash", "mail_date"}:
                if item["category"] == "mail_date" and (mail_cutoff is None or item["modified_at"] >= mail_cutoff):
                    raise CleanupError("El mensaje no cumple el límite de fecha confirmado.")
                # Check the selected message again; no filesystem unlink for mail.
                st = safe_stat(account, item["relative_path"])
                if fingerprint(st) != item["fingerprint"]:
                    raise CleanupError("El mensaje cambió. Vuelve a analizar.")
                mail_groups[(item["account"], item["mailbox_guid"], item["uid_validity"])].append(item)
            else:
                unlink_candidate(account, item)
                deleted.append(item)
        except (OSError, CleanupError) as exc:
            failed.append({"id": req["id"], "path": req.get("relative_path", ""), "error": str(exc),
                           "category": (item or {}).get("category", ""), "bytes": (item or {}).get("reclaimable_bytes", 0),
                           "relative_path": (item or req).get("relative_path", "")})
    for (address, guid, validity), items in mail_groups.items():
        try:
            # GUID + exact UIDs target only the messages selected. UIDVALIDITY
            # was checked against a fresh uidlist in the re-scan above; it is
            # not itself a Dovecot search key.
            query = "UID " + ",".join(str(i["uid"]) for i in items)
            if mail_cutoff is not None:
                query += f" BEFORE {int(mail_cutoff):010d}"
            api.call("Mailboxes", "expunge_messages_for_mailbox_guid", account=address, mailbox_guid=guid, query=query)
            # Success of the API alone is insufficient: observe each file gone.
            for item in items:
                try:
                    safe_stat(account, item["relative_path"])
                    failed.append({"id": item["id"], "path": item["relative_path"], "relative_path": item["relative_path"],
                                   "category": item["category"], "bytes": item["reclaimable_bytes"],
                                   "error": "cPanel aceptó la solicitud; la eliminación aún no pudo verificarse. Vuelve a analizar."})
                except FileNotFoundError:
                    deleted.append(item)
        except (OSError, CleanupError) as exc:
            failed.extend({"id": i["id"], "path": i["relative_path"], "relative_path": i["relative_path"],
                           "category": i["category"], "bytes": i["reclaimable_bytes"], "error": str(exc)} for i in items)
    warnings = list(inventory["warnings"])
    audit_events = [
        {"id": item["id"], "category": item["category"], "relative_path": item["relative_path"],
         "bytes": item["reclaimable_bytes"], "result": "deleted"}
        for item in deleted
    ]
    audit_events.extend(
        {"id": row["id"], "category": row.get("category", ""), "relative_path": row.get("relative_path") or row.get("path", ""),
         "bytes": row.get("bytes", 0), "result": "failed", "error": row.get("error", "")}
        for row in failed
    )
    if audit_events:
        try:
            from cct_audit import append_events
            append_events(account, audit_events, actor=actor, via=via)
        except CleanupError as exc:
            warnings.append(str(exc))
    cache_refresh = api.refresh_disk_usage(rebuild=True) if deleted or mail_groups else None
    after = get_quota(api)
    delta = None
    if before.get("used_bytes") is not None and after.get("used_bytes") is not None:
        delta = before["used_bytes"] - after["used_bytes"]
    return {"deleted_ids": [i["id"] for i in deleted], "failed": [{"id": row["id"], "path": row.get("path") or row.get("relative_path", ""), "error": row["error"]} for row in failed],
            "estimated_removed_bytes": sum(i["reclaimable_bytes"] for i in deleted),
            "quota_before": before, "quota": after, "observed_quota_delta_bytes": delta,
            "cache_refresh": cache_refresh, "warnings": warnings}
