"""Account-scoped cPanel cleanup. Python 3.9+, standard library only."""
import hashlib
import json
import os
import re
import stat
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

VERSION = "0.2.1"
DAY = 86400
MAX_ITEMS = 5000
MAX_VISITED = 150000
SCAN_SECONDS = 20
MAX_DELETE = 250
EXCLUDED = {".ssh", ".gnupg", ".cpanel", ".cagefs", ".git", "node_modules", "vendor", "etc", "ssl", "mail", ".local"}
BACKUP_DIRS = {"backups", "backup", "updraft", "ai1wm-backups", "wpvividbackups", "backupbuddy_backups"}
ARCHIVE = re.compile(r"\.(?:tar(?:\.gz|\.bz2|\.xz)?|tgz|zip|sql(?:\.gz)?|wpress)$", re.I)
ROTATED = re.compile(r"^(?:error_log|access_log|error-log|access-log|.+\.log)(?:(?:[.-]\d[\d._-]*)?\.(?:gz|bz2|xz)|[.-]\d[\d._-]*)$", re.I)
MAILBOXES = {"spam": "spam", "junk": "spam", "trash": "mail_trash"}


class CleanupError(Exception):
    pass


@dataclass(frozen=True)
class Account:
    username: str
    home: Path
    uid: int

    @classmethod
    def current(cls):
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() == 0:
            raise CleanupError("El plugin debe ejecutarse con el usuario de cPanel, sin privilegios root.")
        import pwd
        entry = pwd.getpwuid(os.geteuid())
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", entry.pw_name):
            raise CleanupError("No se pudo identificar una cuenta de cPanel válida.")
        home = Path(entry.pw_dir).resolve(strict=True)
        if home == Path("/") or home.stat().st_uid != entry.pw_uid:
            raise CleanupError("El directorio de la cuenta no es válido.")
        remote = os.environ.get("REMOTE_USER")
        if remote and remote != entry.pw_name:
            raise CleanupError("La sesión y el usuario del proceso no coinciden.")
        return cls(entry.pw_name, home, entry.pw_uid)


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

    def clear_disk_cache(self):
        """API 2 has no UAPI equivalent. Invalidation is not recalculation."""
        try:
            if self.transport is None:
                raise CleanupError("LiveAPI is not connected")
            document = self.transport.call("api2", "DiskUsage", "clearcache", {})
            response = document.get("cpanelresult", document)
            data = response.get("data") or []
            if response.get("event", {}).get("result") != 1 or not data:
                raise ValueError("Cache refresh unavailable")
            status = "cleared" if data[0].get("status") == 1 else "pending" if data[0].get("status") == 0 else "unavailable"
        except (CleanupError, OSError, ValueError, TypeError, KeyError, AttributeError):
            status = "unavailable"
        messages = {"cleared": "Se vació la caché de uso de disco de tu cuenta. cPanel puede tardar en recalcular sus indicadores.",
                    "pending": "cPanel indicó que el vaciado de caché está en curso. Sus indicadores pueden tardar en actualizarse.",
                    "unavailable": "No se pudo vaciar la caché de cPanel. El uso mostrado puede no reflejar todavía los archivos eliminados."}
        return {"status": status, "message": messages[status], "retry_after_seconds": 300,
                "retry_is_guaranteed": False, "source": "DiskUsage/clearcache"}


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


def fingerprint(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink]


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
    return {"id": hashlib.sha256(key.encode()).hexdigest()[:24], "name": PurePosixPath(relative).name,
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
            if address == self.account.username:
                base = "mail"
            elif re.fullmatch(r"[A-Za-z0-9_+.-]+@[A-Za-z0-9.-]+", address):
                local, domain = address.rsplit("@", 1)
                if local in {".", ".."} or domain in {".", ".."}:
                    continue
                base = f"mail/{domain}/{local}"
            else:
                continue
            try:
                statuses = self.api.call("Mailboxes", "get_mailbox_status_list", account=address) or []
                for mailbox in statuses:
                    folder = mailbox.get("mailbox", "")
                    short = folder[6:] if folder.startswith("INBOX.") else folder
                    category = MAILBOXES.get(short.lower())
                    guid = mailbox.get("guid", "")
                    if category and int(mailbox.get("messages", 0)) > 0 and re.fullmatch(r"[a-fA-F0-9]{32}", guid):
                        self.maildir(address, f"{base}/.{short}", guid, category)
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
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != account.uid or st.st_nlink != 1
                or fingerprint(st) != item["fingerprint"]):
            raise CleanupError("El archivo cambió después del análisis. Vuelve a analizar.")
        match = classify(relative, st, time.time())
        if not match or match[1] == "protected":
            raise CleanupError("El archivo ya no es candidato a limpieza.")
        os.unlink(name, dir_fd=fd)


def delete_selected(account, api, requested, confirm, backups_ack=False, scanner_factory=Scanner, mail_cutoff=None):
    if confirm != "ELIMINAR":
        raise CleanupError("Escribe ELIMINAR para confirmar el borrado permanente.")
    if not isinstance(requested, list) or not 1 <= len(requested) <= MAX_DELETE:
        raise CleanupError(f"Selecciona entre 1 y {MAX_DELETE} elementos por operación.")
    if not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in requested):
        raise CleanupError("Selección inválida.")
    if len({i["id"] for i in requested}) != len(requested):
        raise CleanupError("La selección contiene elementos duplicados.")
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
            failed.append({"id": req["id"], "path": req.get("relative_path", ""), "error": str(exc)})
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
                    failed.append({"id": item["id"], "path": item["relative_path"], "error": "cPanel aceptó la solicitud; la eliminación aún no pudo verificarse. Vuelve a analizar."})
                except FileNotFoundError:
                    deleted.append(item)
        except (OSError, CleanupError) as exc:
            failed.extend({"id": i["id"], "path": i["relative_path"], "error": str(exc)} for i in items)
    cache_refresh = api.clear_disk_cache() if deleted or mail_groups else None
    after = get_quota(api)
    delta = None
    if before.get("used_bytes") is not None and after.get("used_bytes") is not None:
        delta = before["used_bytes"] - after["used_bytes"]
    return {"deleted_ids": [i["id"] for i in deleted], "failed": failed,
            "estimated_removed_bytes": sum(i["reclaimable_bytes"] for i in deleted),
            "quota_before": before, "quota": after, "observed_quota_delta_bytes": delta,
            "cache_refresh": cache_refresh, "warnings": inventory["warnings"]}
