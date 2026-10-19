"""Date-scoped mail cleanup: preview exact Maildir UIDs, then revalidate."""
import os
import re
import stat
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from cct_core import (CleanupError, MAILBOXES, Scanner, delete_selected,
                      get_quota, open_parent)

SCOPES = {"spam_trash", "all"}


def normalize_cutoff(value):
    if not isinstance(value, dict):
        raise CleanupError("Selecciona una fecha límite.")
    text, offset = value.get("date"), value.get("utc_offset_minutes")
    hour, minute = value.get("start_hour", 0), value.get("start_minute", 0)
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise CleanupError("La fecha debe tener el formato AAAA-MM-DD.")
    if type(offset) is not int or not -840 <= offset <= 840:
        raise CleanupError("La zona horaria de la fecha no es válida.")
    if type(hour) is not int or type(minute) is not int or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise CleanupError("El inicio del día no es válido.")
    try:
        day = date.fromisoformat(text)
        boundary = datetime(day.year, day.month, day.day, hour, minute,
                            tzinfo=timezone(timedelta(minutes=offset)))
        epoch = int(boundary.timestamp())
    except (ValueError, OverflowError, OSError) as exc:
        raise CleanupError("La fecha límite no es válida.") from exc
    if not 0 <= epoch <= int(time.time()):
        raise CleanupError("La fecha límite debe estar en el pasado o ser el día de hoy.")
    return {"date": text, "utc_offset_minutes": offset, "epoch": epoch,
            "start_hour": hour, "start_minute": minute}


def list_mail_accounts(owner, api):
    records = api.call("Email", "list_pops")
    if not isinstance(records, list):
        raise CleanupError("cPanel no devolvió una lista válida de cuentas de correo.")
    accounts = {owner.username: {"address": owner.username, "main": True}}
    for record in records:
        address = record.get("email") if isinstance(record, dict) else None
        if address == owner.username:
            continue
        if isinstance(address, str) and re.fullmatch(r"[A-Za-z0-9_+.-]+@[A-Za-z0-9.-]+", address):
            local, domain = address.rsplit("@", 1)
            if local not in {".", ".."} and domain not in {".", ".."}:
                accounts[address] = {"address": address, "main": False}
    return list(accounts.values())


def checked_accounts(owner, api, addresses):
    if (not isinstance(addresses, list) or not 1 <= len(addresses) <= 100
            or not all(isinstance(a, str) for a in addresses)
            or len(set(addresses)) != len(addresses)):
        raise CleanupError("Selecciona entre 1 y 100 cuentas de correo distintas.")
    allowed = {a["address"] for a in list_mail_accounts(owner, api)}
    if not set(addresses) <= allowed:
        raise CleanupError("Una cuenta seleccionada no pertenece a tu usuario de cPanel.")
    return addresses


def checked_scope(scope):
    if not isinstance(scope, str) or scope not in SCOPES:
        raise CleanupError("Selecciona spam y papelera o todas las carpetas.")
    return scope


class MailDateScanner(Scanner):
    def __init__(self, account, api, addresses, cutoff, scope, **kwargs):
        super().__init__(account, api, **kwargs)
        self.addresses, self.cutoff, self.scope = addresses, cutoff, scope
        self.mail_cutoff = cutoff["epoch"]

    def _read_guid(self, relative):
        with open_parent(self.account, relative + "/dovecot-uidlist") as (fd, name):
            opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(opened, "r", encoding="utf-8", errors="replace") as stream:
                st = os.fstat(stream.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid != self.account.uid:
                    raise CleanupError("Índice de correo inválido.")
                header = stream.readline(4096).strip().split()
                if not header or header[0] != "3":
                    raise CleanupError("Solo se admite el formato Maildir v3.")
                guid = next((x[1:] for x in header if x.startswith("G")), "")
                return guid.lower() if re.fullmatch(r"[a-fA-F0-9]{32}", guid) else None

    def _paths_by_guid(self, base):
        paths = [base]
        # Match physical folders by GUID instead of guessing encoded folder names.
        # Maildir++ keeps nested/custom-named folders at this level as .a.b.
        with open_parent(self.account, base + "/.cct-anchor") as (fd, _):
            with os.scandir(fd) as entries:
                for entry in entries:
                    self.visited += 1
                    if self.limited():
                        break
                    if entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                        paths.append(base + "/" + entry.name)
        matches = {}
        for relative in paths:
            if self.limited():
                break
            try:
                guid = self._read_guid(relative)
                if guid:
                    # Ambiguous aliases must never identify a deletion target.
                    matches[guid] = relative if guid not in matches else None
            except (OSError, CleanupError):
                continue
        return matches

    def scan(self):
        for address in self.addresses:
            if self.limited():
                break
            base = "mail" if address == self.account.username else "mail/" + "/".join(reversed(address.rsplit("@", 1)))
            try:
                statuses = self.api.call("Mailboxes", "get_mailbox_status_list", account=address)
                if not isinstance(statuses, list):
                    raise CleanupError("No se pudo obtener el listado de carpetas.")
                targets = []
                for mailbox in statuses:
                    name = mailbox.get("mailbox", "")
                    short = name[6:] if name.startswith("INBOX.") else name
                    if self.scope == "spam_trash" and short.lower() not in MAILBOXES:
                        continue
                    if int(mailbox.get("messages", 0)) > 0:
                        targets.append(mailbox)
                if not targets:
                    continue
                paths = self._paths_by_guid(base)
                for mailbox in targets:
                    if self.limited():
                        break
                    guid = mailbox.get("guid", "").lower()
                    relative = paths.get(guid)
                    if not relative:
                        self.partial = True
                        self.warnings.append(f"{address} · {mailbox.get('mailbox', '')}: formato o índice no disponible; carpeta omitida.")
                        continue
                    self.mailbox_name = mailbox["mailbox"]
                    try:
                        self.maildir(address, relative, guid, "mail_date")
                    except (OSError, CleanupError, ValueError) as exc:
                        self.partial = True
                        self.warnings.append(f"{address} · {self.mailbox_name}: {exc}")
            except (OSError, CleanupError, ValueError) as exc:
                self.partial = True
                self.warnings.append(f"{address}: {exc}")
        unique = {item["id"]: item for item in self.items}
        protected = sum(not item["selectable"] for item in unique.values())
        items = [i for i in unique.values() if i["selectable"]]
        summaries = []
        for address in self.addresses:
            rows = [i for i in items if i["account"] == address]
            folders = defaultdict(lambda: {"messages": 0, "bytes": 0})
            for item in rows:
                folders[item["mailbox"]]["messages"] += 1
                folders[item["mailbox"]]["bytes"] += item["reclaimable_bytes"]
            summaries.append({"address": address, "messages": len(rows),
                "estimated_bytes": sum(i["reclaimable_bytes"] for i in rows),
                "folders": [{"name": name, **values} for name, values in folders.items()]})
        if self.partial:
            self.warnings.append("Vista previa parcial: solo se eliminarán los mensajes identificados aquí. Vuelve a analizar después para continuar.")
        if protected:
            self.warnings.append(f"{protected} mensajes protegidos se excluyeron de la selección y del ahorro estimado.")
        return {"items": items, "accounts": summaries, "cutoff": self.cutoff, "scope": self.scope,
                "messages": len(items), "estimated_bytes": sum(i["reclaimable_bytes"] for i in items),
                "partial": self.partial, "warnings": list(dict.fromkeys(self.warnings)),
                "scanned_at": self.now, "visited": self.visited}


def preview_mail_by_date(owner, api, addresses, cutoff, scope, scanner_factory=MailDateScanner):
    cutoff, scope = normalize_cutoff(cutoff), checked_scope(scope)
    addresses = checked_accounts(owner, api, addresses)
    result = scanner_factory(owner, api, addresses, cutoff, scope).scan()
    result["quota"] = get_quota(api)
    return result


def delete_mail_by_date(owner, api, requested, cutoff, scope, confirm,
                        irreversible_ack, all_folders_ack=False, scanner_factory=MailDateScanner):
    if confirm != "ELIMINAR CORREOS" or irreversible_ack is not True:
        raise CleanupError("Confirma que el borrado es definitivo y escribe ELIMINAR CORREOS.")
    cutoff, scope = normalize_cutoff(cutoff), checked_scope(scope)
    if scope == "all" and all_folders_ack is not True:
        raise CleanupError("Confirma que el alcance incluye recibidos, enviados, borradores y otras carpetas.")
    if (not isinstance(requested, list) or not 1 <= len(requested) <= 250
            or not all(isinstance(i, dict) and isinstance(i.get("account"), str) for i in requested)):
        raise CleanupError("La selección de mensajes no es válida (máximo 250 por lote).")
    addresses = checked_accounts(owner, api, sorted({i["account"] for i in requested}))
    factory = lambda account, client: scanner_factory(account, client, addresses, cutoff, scope)
    return delete_selected(owner, api, requested, "ELIMINAR", scanner_factory=factory,
                           mail_cutoff=cutoff["epoch"])
