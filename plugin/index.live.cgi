#!/usr/bin/python3
"""cPanel authenticated CGI entry point. No root, tokens, or user-supplied home."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from cct_core import Account, CleanupError, Scanner, UAPI, delete_selected, get_quota
from cct_mail import list_mail_accounts, preview_mail_by_date, delete_mail_by_date


def respond(body, status="200 OK", content_type="application/json; charset=utf-8"):
    headers = ["Status: " + status, "Content-Type: " + content_type,
               "Cache-Control: no-store", "X-Content-Type-Options: nosniff",
               "Referrer-Policy: no-referrer",
               "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"]
    sys.stdout.write("\r\n".join(headers) + "\r\n\r\n")
    sys.stdout.write(body if isinstance(body, str) else json.dumps(body, ensure_ascii=True))


def check_post(environ):
    if environ.get("REQUEST_METHOD") != "POST":
        raise CleanupError("Esta operación requiere POST.")
    if environ.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json" or environ.get("HTTP_X_CCT_REQUEST") != "1":
        raise CleanupError("Solicitud inválida. Abre el plugin desde cPanel.")
    if environ.get("HTTP_SEC_FETCH_SITE") in {"cross-site", "same-site"}:
        raise CleanupError("La solicitud debe proceder del mismo origen.")
    origin = environ.get("HTTP_ORIGIN")
    if origin:
        expected = ("https" if environ.get("HTTPS", "").lower() in {"on", "1"} or environ.get("SERVER_PORT") == "2083" else "http") + "://" + environ.get("HTTP_HOST", "")
        if origin != expected:
            raise CleanupError("El origen de la solicitud no coincide con cPanel.")


def main():
    action = parse_qs(os.environ.get("QUERY_STRING", "")).get("action", ["page"])[0]
    try:
        account = Account.current()
        api = UAPI(account)
        api.require_feature()
        if action == "page" and os.environ.get("REQUEST_METHOD", "GET") == "GET":
            respond((BASE / "index.html").read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
        elif action == "scan" and os.environ.get("REQUEST_METHOD") == "GET":
            result = Scanner(account, api).scan()
            result["quota"] = get_quota(api)
            respond(result)
        elif action == "quota" and os.environ.get("REQUEST_METHOD") == "GET":
            respond({"quota": get_quota(api)})
        elif action == "refresh":
            check_post(os.environ)
            refresh = api.clear_disk_cache()
            respond({"cache_refresh": refresh, "quota": get_quota(api)})
        elif action == "mail_accounts" and os.environ.get("REQUEST_METHOD") == "GET":
            respond({"accounts": list_mail_accounts(account, api)})
        elif action in {"delete", "mail_preview", "mail_delete"}:
            check_post(os.environ)
            size = int(os.environ.get("CONTENT_LENGTH", "0"))
            if not 0 < size <= 1024 * 1024:
                raise CleanupError("El tamaño de la solicitud no es válido.")
            payload = json.loads(sys.stdin.buffer.read(size))
            if not isinstance(payload, dict):
                raise CleanupError("Solicitud inválida.")
            if action == "delete":
                respond(delete_selected(account, api, payload.get("items"), payload.get("confirm"), payload.get("backups_ack", False)))
            elif action == "mail_preview":
                respond(preview_mail_by_date(account, api, payload.get("accounts"), payload.get("cutoff"), payload.get("scope")))
            else:
                respond(delete_mail_by_date(account, api, payload.get("items"), payload.get("cutoff"), payload.get("scope"), payload.get("confirm"), payload.get("irreversible_ack"), payload.get("all_folders_ack", False)))
        else:
            respond({"error": "Operación no disponible."}, "404 Not Found")
    except (CleanupError, ValueError) as exc:
        respond({"error": str(exc)}, "400 Bad Request")
    except Exception:
        # Do not expose session data, server paths or tracebacks to the client.
        respond({"error": "No se pudo completar la operación. Vuelve a analizar o contacta al administrador."}, "500 Internal Server Error")


if __name__ == "__main__":
    main()
