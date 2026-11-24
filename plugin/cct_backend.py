#!/usr/bin/python3
"""Account-scoped worker. Only index.live.pl speaks to cPanel LiveAPI."""
import base64
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from cct_core import Account, CleanupError, Scanner, UAPI, delete_selected, get_quota, get_storage, list_whm_accounts
from cct_mail import list_mail_accounts, preview_mail_by_date, delete_mail_by_date
from cct_live import PipeTransport
from cct_batch import BatchScan, recheck_selection
from cct_audit import list_months, read_month


def respond(body, status="200 OK", content_type="application/json; charset=utf-8"):
    return {"type": "response", "status": status, "content_type": content_type,
            "body": body if isinstance(body, str) else json.dumps(body, ensure_ascii=True)}


def check_post(environ):
    if environ.get("REQUEST_METHOD") != "POST":
        raise CleanupError("Esta operación requiere POST.")
    if environ.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json" or environ.get("HTTP_X_CCT_REQUEST") != "1":
        raise CleanupError("Solicitud inválida. Abre el plugin desde cPanel o WHM.")
    if environ.get("HTTP_SEC_FETCH_SITE") in {"cross-site", "same-site"}:
        raise CleanupError("La solicitud debe proceder del mismo origen.")
    origin = environ.get("HTTP_ORIGIN")
    if origin:
        secure = environ.get("HTTPS", "").lower() in {"on", "1"} or environ.get("SERVER_PORT") in {"2083", "2087"}
        expected = ("https" if secure else "http") + "://" + environ.get("HTTP_HOST", "")
        if origin != expected:
            raise CleanupError("El origen de la solicitud no coincide con la sesión.")


def drop_to_account(account):
    """WHM starts as root; filesystem work must run as the target account."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    import pwd
    entry = pwd.getpwuid(account.uid)
    if hasattr(os, "setgroups"):
        os.setgroups([])
    os.setgid(entry.pw_gid)
    os.setuid(account.uid)
    if os.geteuid() != account.uid:
        raise CleanupError("No se pudo impersonar la cuenta seleccionada.")


def main(transport=None, body_bytes=b""):
    query = parse_qs(os.environ.get("QUERY_STRING", ""))
    action = query.get("action", ["page"])[0]
    try:
        via = "whm" if os.environ.get("CCT_VIA") == "whm" else "cpanel"
        if action == "accounts" and via == "whm" and os.environ.get("REQUEST_METHOD") == "GET":
            if not hasattr(os, "geteuid") or os.geteuid() != 0:
                raise CleanupError("Solo WHM root puede listar cuentas.")
            from cct_core import VERSION
            return respond({"accounts": list_whm_accounts(UAPI(None, transport=transport)), "version": VERSION})
        account = Account.resolve()
        if via == "whm":
            drop_to_account(account)
        api = UAPI(account, transport=transport)
        if via == "cpanel":
            api.require_feature()
        if action == "page" and os.environ.get("REQUEST_METHOD", "GET") == "GET":
            if query.get("embedded") != ["1"]:
                response = respond("", status="302 Found", content_type="text/html; charset=utf-8")
                response["location"] = "home.html"
                return response
            return respond((BASE / "index.html").read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
        elif action == "scan" and os.environ.get("REQUEST_METHOD") == "GET":
            result = Scanner(account, api).scan()
            result["quota"] = get_quota(api)
            return respond(result)
        elif action == "quota" and os.environ.get("REQUEST_METHOD") == "GET":
            return respond({"quota": get_quota(api), "account": {"username": account.username}})
        elif action == "storage" and os.environ.get("REQUEST_METHOD") == "GET":
            return respond({"storage": get_storage(api)})
        elif action == "audit_months" and os.environ.get("REQUEST_METHOD") == "GET":
            return respond({"months": list_months(account)})
        elif action == "audit_read" and os.environ.get("REQUEST_METHOD") == "GET":
            month = query.get("month", [None])[0]
            return respond(read_month(account, month))
        elif action == "refresh":
            check_post(os.environ)
            size = int(os.environ.get("CONTENT_LENGTH") or "0")
            if size < 0 or size > 1024 * 1024:
                raise CleanupError("El tamaño de la solicitud no es válido.")
            if size and len(body_bytes) != size:
                raise CleanupError("El cuerpo de la solicitud está incompleto.")
            payload = json.loads(body_bytes) if size else {}
            if not isinstance(payload, dict):
                raise CleanupError("Solicitud inválida.")
            poll = payload.get("poll") is True
            refresh = api.refresh_disk_usage(rebuild=True, poll=poll)
            return respond({"cache_refresh": refresh, "quota": get_quota(api), "storage": get_storage(api)})
        elif action == "mail_accounts" and os.environ.get("REQUEST_METHOD") == "GET":
            return respond({"accounts": list_mail_accounts(account, api)})
        elif action in {"delete", "mail_preview", "mail_delete", "scan_start", "scan_step", "scan_results"}:
            check_post(os.environ)
            size = int(os.environ.get("CONTENT_LENGTH", "0"))
            if not 0 < size <= 1024 * 1024:
                raise CleanupError("El tamaño de la solicitud no es válido.")
            if len(body_bytes) != size:
                raise CleanupError("El cuerpo de la solicitud está incompleto.")
            payload = json.loads(body_bytes)
            if not isinstance(payload, dict):
                raise CleanupError("Solicitud inválida.")
            if action in {"scan_start", "scan_step", "scan_results"}:
                with BatchScan(account, api) as job:
                    if action == "scan_start":
                        return respond({"scan": job.start(), "quota": get_quota(api)})
                    if action == "scan_step":
                        return respond({"scan": job.step(payload.get("token"))})
                    result = job.results(payload.get("token"), payload.get("offset", 0))
                    if payload.get("offset", 0) == 0:
                        result["quota"] = get_quota(api)
                    return respond(result)
            actor = "root" if via == "whm" else f"cpanel:{account.username}"
            if action == "delete" and payload.get("analysis_token"):
                with BatchScan(account, api) as job:
                    approved = job.approved(payload["analysis_token"], payload.get("items"))
                from types import SimpleNamespace
                factory = lambda account, api: SimpleNamespace(scan=lambda: recheck_selection(account, api, approved))
                return respond(delete_selected(account, api, payload.get("items"), payload.get("confirm"), payload.get("backups_ack", False),
                                              scanner_factory=factory, actor=actor, via=via))
            elif action == "delete":
                return respond(delete_selected(account, api, payload.get("items"), payload.get("confirm"), payload.get("backups_ack", False),
                                              actor=actor, via=via))
            elif action == "mail_preview":
                return respond(preview_mail_by_date(account, api, payload.get("accounts"), payload.get("cutoff"), payload.get("scope")))
            else:
                return respond(delete_mail_by_date(
                    account, api, payload.get("items"), payload.get("cutoff"), payload.get("scope"),
                    payload.get("confirm"), payload.get("irreversible_ack"), payload.get("all_folders_ack", False),
                    actor=actor, via=via))

        else:
            return respond({"error": "Operación no disponible."}, "404 Not Found")
    except (CleanupError, ValueError) as exc:
        return respond({"error": str(exc)}, "400 Bad Request")
    except Exception:
        # Do not expose session data, server paths or tracebacks to the client.
        return respond({"error": "No se pudo completar la operación. Vuelve a analizar o contacta al administrador."}, "500 Internal Server Error")


if __name__ == "__main__":
    transport = PipeTransport()
    try:
        start = transport.read_frame()
        if start.get("type") != "start":
            raise CleanupError("Abre el plugin desde cPanel.")
        body = base64.b64decode(start.get("body_b64", ""), validate=True)
        response = main(transport, body)
    except Exception:
        response = respond({"error": "No se pudo iniciar la conexión del plugin con cPanel."}, "500 Internal Server Error")
    transport.write_frame(response)
