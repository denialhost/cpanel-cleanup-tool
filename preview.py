"""Local, synthetic preview. No account files or real deletion are accessed."""
import argparse
import copy
import json
import mimetypes
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "plugin"
sys.path.insert(0, str(PLUGIN))
from cct_core import CleanupError, MAILBOXES, same_item
from cct_mail import normalize_cutoff, checked_scope
GIB = 1024**3
MIB = 1024**2


def fixture():
    now = time.time()
    data = [
        ("backups/backup-2026-08-01.tar.gz", 1.82*GIB, 79, "backups", "review", "Confirma que tienes una copia vigente antes de eliminar este respaldo."),
        ("backups/backup-2026-10-18.tar.gz", 2.1*GIB, 1, "backups", "protected", "Se conserva el respaldo más reciente de esta carpeta."),
        ("logs/tienda.cl-ssl_log-Sep-2026.gz", 842*MIB, 33, "logs", "candidate", "Registro rotado sin modificaciones durante al menos 7 días."),
        (".trash/catalogo-anterior.zip", 468*MIB, 48, "trash", "review", "Archivo en la papelera del administrador de archivos."),
        ("public_html/wp-content/updraft/backup-2026-07-15-db.sql.gz", 318*MIB, 96, "backups", "review", "Revisa los respaldos: este puede ser parte de un conjunto de varios archivos."),
        ("public_html/wp-content/updraft/backup-2026-10-17-db.sql.gz", 334*MIB, 2, "backups", "protected", "Se conserva el respaldo más reciente de esta carpeta."),
        ("logs/tienda.cl-Aug-2026.gz", 241*MIB, 63, "logs", "candidate", "Registro rotado antiguo."),
        (".trash/imagenes-campana-2025.zip", 196*MIB, 121, "trash", "review", "Archivo en la papelera del administrador de archivos."),
        ("mail/tienda.cl/ventas/.Junk/cur/1751000021.M932:2,S", 24*MIB, 42, "spam", "review", "Mensaje en spam. Eliminación mediante cPanel por su UID."),
        ("mail/tienda.cl/ventas/.Trash/cur/1751000321.M102:2,S", 18.4*MIB, 51, "mail_trash", "review", "Mensaje en la papelera de correo."),
        ("mail/tienda.cl/contacto/.Junk/cur/1751000721.M122:2,S", 12.8*MIB, 20, "spam", "review", "Mensaje en spam."),
        ("mail/tienda.cl/contacto/.Trash/cur/1751000921.M222:2,S", 8.2*MIB, 11, "mail_trash", "review", "Mensaje en la papelera de correo."),
        ("logs/error_log.3.gz", 76*MIB, 26, "logs", "candidate", "Registro rotado antiguo."),
        ("public_html/error_log", 126*MIB, 0, "logs", "protected", "Puede ser un registro activo. Requiere rotación antes de limpiarlo."),
        (".trash/informe-antiguo.pdf", 6.8*MIB, 32, "trash", "review", "Archivo en la papelera."),
        ("logs/error_log.1.gz", 18*MIB, 2, "logs", "protected", "Registro reciente: conservar durante al menos 7 días."),
    ]
    items = []
    for n, (path, size, days, category, risk, reason) in enumerate(data):
        item = {"id": f"demo-{n}", "name": path.rsplit("/",1)[-1], "relative_path":path,
                "path":"/home/tienda/"+path, "category":category, "risk":risk,
                "selectable":risk!="protected", "reason":reason, "size_bytes":int(size),
                "reclaimable_bytes":int(size), "modified_at":now-days*86400,
                "fingerprint":[0,n,int(size),int(now-days*86400)*10**9,0,1]}
        if category in {"spam","mail_trash"}:
            item.update(account="ventas@tienda.cl" if "/ventas/" in path else "contacto@tienda.cl",uid=n+100,mailbox_guid="a"*32,uid_validity="1")
        items.append(item)
    limit,used=20*GIB,int(17.6*GIB)
    mail_items=[]
    for item in items:
        if item["category"] in {"spam","mail_trash"}:
            row=copy.deepcopy(item)
            row.update(category="mail_date",mailbox="INBOX.Junk" if item["category"]=="spam" else "INBOX.Trash")
            mail_items.append(row)
    examples=[("ventas@tienda.cl","INBOX",14,5), ("ventas@tienda.cl","INBOX",410,42),
              ("ventas@tienda.cl","INBOX.Sent",680,68), ("ventas@tienda.cl","INBOX.Archivo.2024",760,126),
              ("ventas@tienda.cl","INBOX.Junk",410,8), ("ventas@tienda.cl","INBOX.Trash",500,12),
              ("ventas@tienda.cl","INBOX",62,34), ("ventas@tienda.cl","INBOX.Sent",90,56),
              ("contacto@tienda.cl","INBOX",430,26), ("contacto@tienda.cl","INBOX.Sent",740,41),
              ("contacto@tienda.cl","INBOX.Trash",490,7), ("contacto@tienda.cl","INBOX",10,3),
              ("contacto@tienda.cl","INBOX.Proyectos.Niño",510,55), ("tienda","INBOX",520,11),
              ("tienda","INBOX.Drafts",450,2), ("tienda","INBOX.Trash",470,4)]
    for n,(address,folder,age,size_mib) in enumerate(examples):
        local,domain=address.rsplit('@',1) if '@' in address else ('','')
        base=f"mail/{domain}/{local}" if domain else "mail"
        location=base+("" if folder=="INBOX" else "/."+folder[6:])+f"/cur/demo-message-{n}:2,S"
        size=int(size_mib*MIB)
        mail_items.append({"id":f"mail-demo-{n}","name":f"demo-message-{n}","relative_path":location,"path":"/home/tienda/"+location,
            "category":"mail_date","risk":"review","selectable":True,"reason":"Mensaje anterior a la fecha elegida.",
            "size_bytes":size,"reclaimable_bytes":size,"modified_at":now-age*86400,"fingerprint":[0,1000+n,size,int(now-age*86400)*10**9,0,1],
            "account":address,"uid":200+n,"uid_validity":"42","mailbox_guid":f"{n+1:032x}","mailbox":folder})
    return {"items":items,"mail_items":mail_items,"partial":False,"warnings":[],"scanned_at":now,"visited":8421,
            "account":{"username":"tienda","home":"/home/tienda"},"version":"0.2.0",
            "quota":{"limit_bytes":limit,"used_bytes":used,"free_bytes":limit-used,"source":"demo"}}


DEMO_ACCOUNTS=[{"address":"tienda","main":True},{"address":"ventas@tienda.cl","main":False},{"address":"contacto@tienda.cl","main":False}]


def demo_mail_preview(snapshot, addresses, cutoff, scope):
    cutoff,scope=normalize_cutoff(cutoff),checked_scope(scope)
    if not isinstance(addresses,list) or not addresses or not all(isinstance(a,str) for a in addresses) or not set(addresses)<={a['address'] for a in DEMO_ACCOUNTS}:
        raise CleanupError("Selecciona cuentas de correo válidas.")
    rows=[i for i in snapshot['mail_items'] if i['account'] in addresses and i['modified_at']<cutoff['epoch'] and
          (scope=='all' or i['mailbox'].removeprefix('INBOX.').lower() in MAILBOXES)]
    summaries=[]
    for address in addresses:
        selected=[i for i in rows if i['account']==address]
        folders=[]
        for folder in dict.fromkeys(i['mailbox'] for i in selected):
            group=[i for i in selected if i['mailbox']==folder]
            folders.append({'name':folder,'messages':len(group),'bytes':sum(i['reclaimable_bytes'] for i in group)})
        summaries.append({'address':address,'messages':len(selected),'estimated_bytes':sum(i['reclaimable_bytes'] for i in selected),'folders':folders})
    return {'items':rows,'accounts':summaries,'cutoff':cutoff,'scope':scope,'messages':len(rows),
            'estimated_bytes':sum(i['reclaimable_bytes'] for i in rows),'partial':False,'warnings':[],
            'scanned_at':time.time(),'visited':len(snapshot['mail_items']),'quota':copy.deepcopy(snapshot['quota'])}


class Preview(BaseHTTPRequestHandler):
    snapshot = fixture()

    def log_message(self, *_):
        pass

    def send(self, data, status=200, content_type="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        return self.headers.get("Host", "").split(":")[0] in {"127.0.0.1", "localhost"}

    def do_GET(self):
        if not self.allowed():
            return self.send({"error":"Host inválido."},403)
        path = urlsplit(self.path).path
        if path == "/api/scan":
            result=copy.deepcopy(self.snapshot)
            result.pop("mail_items",None)
            result["scanned_at"]=time.time()
            return self.send(result)
        if path == "/api/quota":
            return self.send({"quota":self.snapshot["quota"]})
        if path == "/api/mail_accounts":
            return self.send({"accounts":DEMO_ACCOUNTS})
        filename = "index.html" if path == "/" else path.lstrip("/")
        if filename not in {"index.html","styles.css","app.mjs","model.mjs","mail.mjs","mail-model.mjs","icon.svg"}:
            return self.send({"error":"No disponible."},404)
        body=(PLUGIN/filename).read_bytes()
        if filename == "index.html":
            body=body.replace(b'data-demo="false"',b'data-demo="true"')
        content_type={".mjs":"text/javascript; charset=utf-8",".css":"text/css; charset=utf-8",".html":"text/html; charset=utf-8",".svg":"image/svg+xml"}[Path(filename).suffix]
        self.send(body,content_type=content_type)

    def do_POST(self):
        origin=self.headers.get("Origin")
        expected="http://"+self.headers.get("Host","")
        if not self.allowed() or (origin and origin!=expected) or self.headers.get("X-CCT-Request")!="1":
            return self.send({"error":"Solicitud inválida."},403)
        try:
            length=int(self.headers.get("Content-Length","0"))
            if not 0 < length <= 1024*1024:
                raise ValueError()
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):
                raise ValueError()
            path=urlsplit(self.path).path
            if path=="/api/reset":
                type(self).snapshot=fixture()
                return self.send({"ok":True})
            if path=="/api/refresh":
                return self.send({"quota":self.snapshot["quota"],"cache_refresh":{"status":"cleared","message":"Simulación: se vació la caché de uso de disco de tu cuenta.","retry_after_seconds":300,"retry_is_guaranteed":False}})
            if path=="/api/mail_preview":
                return self.send(demo_mail_preview(self.snapshot,payload.get('accounts'),payload.get('cutoff'),payload.get('scope')))
            if path not in {"/api/delete","/api/mail_delete"}:
                return self.send({"error":"Operación inválida."},404)
            requested=payload.get("items",[])
            if not isinstance(requested,list) or not 1<=len(requested)<=250:
                raise ValueError()
            ids={item["id"] for item in requested}
            if path=="/api/mail_delete":
                if payload.get('confirm')!='ELIMINAR CORREOS' or payload.get('irreversible_ack') is not True or (payload.get('scope')=='all' and payload.get('all_folders_ack') is not True):
                    return self.send({'error':'Confirma el borrado permanente y el alcance.'},400)
                result=demo_mail_preview(self.snapshot,sorted({i['account'] for i in requested}),payload.get('cutoff'),payload.get('scope'))
                eligible={i['id']:i for i in result['items']}
                selected=[eligible[i['id']] for i in requested if i['id'] in eligible and same_item(i,eligible[i['id']])]
            else:
                if payload.get("confirm")!="ELIMINAR":
                    return self.send({"error":"Confirmación necesaria."},400)
                selected=[i for i in self.snapshot["items"] if i["id"] in ids and i["selectable"] and (i["category"]!="backups" or payload.get("backups_ack") is True)]
            size=sum(i["reclaimable_bytes"] for i in selected)
            deleted={i["id"] for i in selected}
            self.snapshot["items"][:]=[i for i in self.snapshot["items"] if i["id"] not in deleted]
            self.snapshot["mail_items"][:]=[i for i in self.snapshot["mail_items"] if i["id"] not in deleted]
            before=copy.deepcopy(self.snapshot["quota"])
            self.snapshot["quota"]["used_bytes"]-=size
            self.snapshot["quota"]["free_bytes"]+=size
            return self.send({"deleted_ids":sorted(deleted),"estimated_removed_bytes":size,"failed":[],"warnings":[],"quota_before":before,"quota":self.snapshot["quota"],"observed_quota_delta_bytes":size,"cache_refresh":{"status":"cleared","message":"Simulación: se vació la caché de uso de disco de tu cuenta.","retry_after_seconds":300,"retry_is_guaranteed":False}})
        except (CleanupError,ValueError,TypeError,KeyError):
            self.send({"error":"Solicitud inválida."},400)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Vista previa CCT con datos ficticios, sin borrado real.")
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    server=ThreadingHTTPServer(("127.0.0.1",args.port),Preview)
    print(f"Vista previa: http://127.0.0.1:{server.server_address[1]}",flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
