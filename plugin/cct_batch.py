"""Resumable, account-owned analysis; requests never carry filesystem cursors."""
import bisect
import hmac
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath

import cct_core as core

STEP_SECONDS = 3
STEP_ENTRIES = 10000
MAX_RESULTS = 50000
PAGE_SIZE = 250
TTL = 3600


def possible_file(relative):
    parts = PurePosixPath(relative).parts
    name = parts[-1]
    return (parts[0] == '.trash' or core.ROTATED.search(name) or
            name.lower() in {'error_log', 'access_log', 'error-log', 'access-log'} or
            name.lower().endswith('.log') or
            (parts[0] == 'logs' and name.lower().endswith(('.gz', '.bz2', '.xz'))) or
            (core.ARCHIVE.search(name) and
             (any(p.lower() in core.BACKUP_DIRS for p in parts[:-1]) or
              (len(parts) == 1 and name.startswith('backup-')))))


def retain_backups(items, partial=False):
    groups = defaultdict(list)
    for item in items:
        if item['category'] == 'backups':
            groups[str(PurePosixPath(item['relative_path']).parent)].append(item)
    for group in groups.values():
        newest = max(group, key=lambda item: (item['modified_at'], item['relative_path']))
        stamp = re.search(r'20\d{2}[-_]\d{2}[-_]\d{2}', newest['name'])
        for item in group:
            if partial or item['modified_at'] >= newest['modified_at'] - 2 * core.DAY or (stamp and stamp.group() in item['name']):
                item.update(risk='protected', selectable=False, reason=(
                    'Análisis incompleto: respaldos protegidos.' if partial else
                    'Se conservan los respaldos del conjunto más reciente (ventana de 48 horas).'))


class BatchScan:
    def __init__(self, account, api, directory=None, seconds=STEP_SECONDS, entries=STEP_ENTRIES):
        self.account, self.api = account, api
        self.directory = Path(directory or '/tmp/cct-batch-' + str(account.uid))
        self.seconds, self.entries = seconds, entries

    def __enter__(self):
        if os.name != 'posix':
            raise core.CleanupError('El análisis por lotes requiere Linux.')
        import fcntl
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        self.dirfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.lockfd = None
        self.db = None
        try:
            self._private(os.fstat(self.dirfd))
            self.lockfd = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=self.dirfd)
            self._private(os.fstat(self.lockfd), regular=True)
            lock_deadline = time.monotonic() + 4
            while True:
                try:
                    fcntl.flock(self.lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= lock_deadline:
                        raise core.CleanupError('El análisis ya está procesando otro lote. Reintenta en unos segundos.') from exc
                    time.sleep(0.05)
            fd = os.open('scan.db', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=self.dirfd)
            try:
                self._private(os.fstat(fd), regular=True)
            finally:
                os.close(fd)
            self.db = sqlite3.connect('/proc/self/fd/' + str(self.dirfd) + '/scan.db', timeout=1)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA max_page_count=32768')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS dirs (path TEXT PRIMARY KEY, after_name TEXT, done INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, category TEXT, task INTEGER, data TEXT);
                CREATE TABLE IF NOT EXISTS accounts (address TEXT PRIMARY KEY, done INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS boxes (id INTEGER PRIMARY KEY, address TEXT, path TEXT, guid TEXT,
                    category TEXT, phase TEXT DEFAULT 'index', pos INTEGER DEFAULT 0, after_name TEXT,
                    validity TEXT, signature TEXT, restarts INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS uids (task INTEGER, base TEXT, uid INTEGER, PRIMARY KEY(task,base));
            ''')
            self.meta = {row['key']: json.loads(row['value']) for row in self.db.execute('SELECT * FROM meta')}
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def _private(self, info, regular=False):
        if info.st_uid != self.account.uid or info.st_mode & 0o077 or (regular and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
            raise core.CleanupError('El almacenamiento temporal del análisis no es seguro.')

    def __exit__(self, *unused):
        if self.db is not None:
            self.db.close()
        if self.lockfd is not None:
            os.close(self.lockfd)
        os.close(self.dirfd)

    def _save(self):
        self.db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)',
                            [(key, json.dumps(value)) for key, value in self.meta.items()])
        self.db.commit()

    def _check(self, token):
        if (not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token) or
                not hmac.compare_digest(token, self.meta.get('token', ''))):
            raise core.CleanupError('El análisis ya no está disponible. Vuelve a analizar.')
        if self.meta.get('account_key') != self._account_key():
            raise core.CleanupError('El análisis pertenece a otra cuenta. Vuelve a analizar.')
        if time.time() - self.meta.get('created', 0) > TTL:
            raise core.CleanupError('El análisis venció. Vuelve a analizar.')

    def start(self):
        if self.meta.get('token') and self.meta.get('phase') != 'done' and self.meta.get('walk_format') == 'stack1':
            try:
                self._check(self.meta['token'])
                row = self.db.execute('SELECT path FROM dirs LIMIT 1').fetchone()
                if row is not None:
                    json.loads(row['path'])
                return self.status()
            except (core.CleanupError, ValueError):
                pass
        for table in ('meta', 'dirs', 'items', 'accounts', 'boxes', 'uids'):
            self.db.execute('DELETE FROM ' + table)
        self.meta = dict(token=secrets.token_hex(32), created=time.time(), phase='files', account_key=self._account_key(), walk_format='stack1',
                         files_visited=0, mail_visited=0, directories=0, warnings=[], partial=False,
                         accounts_loaded=False, candidates=0, batches=0)
        self.db.execute('INSERT INTO dirs(path) VALUES (?)', (json.dumps(''),))
        self._save()
        if self.db.execute('PRAGMA freelist_count').fetchone()[0] > 256:
            self.db.execute('VACUUM')
        return self.status()

    def _account_key(self):
        return hashlib.sha256((self.account.username + '\0' + str(self.account.home)).encode()).hexdigest()

    def status(self):
        total = self.db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
        done = self.db.execute('SELECT COUNT(*) FROM accounts WHERE done=1').fetchone()[0]
        return {**self.meta, 'done': self.meta.get('phase') == 'done',
                'visited': self.meta.get('files_visited', 0) + self.meta.get('mail_visited', 0),
                'mail_accounts_total': total, 'mail_accounts_done': done,
                'account': {'username': self.account.username, 'home': str(self.account.home)},
                'version': core.VERSION}

    def _warn(self, message):
        self.meta['partial'] = True
        if message not in self.meta['warnings'] and len(self.meta['warnings']) < 20:
            self.meta['warnings'].append(message)

    def _limited(self):
        return self.processed >= self.entries or time.monotonic() >= self.deadline

    def _item(self, item, task=None):
        if self.meta['candidates'] >= MAX_RESULTS:
            self._warn('Se muestran los primeros 50.000 elementos. Limpia una selección y vuelve a analizar para obtener más resultados.')
            return
        inserted = self.db.execute('INSERT OR IGNORE INTO items VALUES (?,?,?,?)',
                                   (item['id'], item['category'], task, json.dumps(item))).rowcount
        self.meta['candidates'] += inserted

    def _files(self):
        # Keep only the active depth-first branch, rather than queuing every
        # descendant directory. Very wide trees otherwise inflate the cache.
        cache = {}
        try:
            while not self._limited():
                directory = self.db.execute('SELECT * FROM dirs ORDER BY rowid DESC LIMIT 1').fetchone()
                if directory is None:
                    self.meta['phase'] = 'mail'
                    return
                key = directory['path']
                relative = json.loads(key)
                try:
                    if key not in cache:
                        if len(cache) >= 64:
                            oldest = next(iter(cache))
                            cache.pop(oldest)['manager'].__exit__(None, None, None)
                        manager = core.open_parent(self.account, (relative + '/' if relative else '') + '.cct-anchor')
                        fd, unused = manager.__enter__()
                        try:
                            with os.scandir(fd) as stream:
                                entries = sorted(stream, key=lambda entry: entry.name)
                        except Exception:
                            manager.__exit__(None, None, None)
                            raise
                        position = bisect.bisect_right([entry.name for entry in entries], json.loads(directory['after_name'])) if directory['after_name'] is not None else 0
                        cache[key] = {'manager': manager, 'entries': entries, 'position': position}
                    frame = cache[key]
                    last = None
                    while frame['position'] < len(frame['entries']) and not self._limited():
                        entry = frame['entries'][frame['position']]
                        frame['position'] += 1
                        last = entry.name
                        self.processed += 1
                        self.meta['files_visited'] += 1
                        path = (relative + '/' if relative else '') + entry.name
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name not in core.EXCLUDED:
                                    self.db.execute('INSERT OR IGNORE INTO dirs(path) VALUES (?)', (json.dumps(path),))
                                    break
                            elif possible_file(path):
                                info = entry.stat(follow_symlinks=False)
                                match = core.classify(path, info, self.meta['created'])
                                if stat.S_ISREG(info.st_mode) and match:
                                    self._item(core.make_item(self.account, path, info, *match))
                        except FileNotFoundError:
                            continue
                        except OSError as exc:
                            self._warn('No se pudo revisar ' + path + ' (error ' + str(exc.errno) + ').')
                    if frame['position'] == len(frame['entries']):
                        self.db.execute('DELETE FROM dirs WHERE path=?', (key,))
                        cache.pop(key)['manager'].__exit__(None, None, None)
                        self.meta['directories'] += 1
                    elif last is not None:
                        self.db.execute('UPDATE dirs SET after_name=? WHERE path=?', (json.dumps(last), key))
                except OSError as exc:
                    self._warn('No se pudo abrir ' + (relative or 'la carpeta principal') + ' (error ' + str(exc.errno) + ').')
                    self.db.execute('DELETE FROM dirs WHERE path=?', (key,))
                    if key in cache:
                        cache.pop(key)['manager'].__exit__(None, None, None)
        finally:
            for frame in cache.values():
                frame['manager'].__exit__(None, None, None)

    def _accounts(self):
        if not self.meta['accounts_loaded']:
            try:
                rows = self.api.call('Email', 'list_pops') or []
                addresses = {self.account.username}
                addresses.update(row.get('email', '') for row in rows if isinstance(row, dict))
                addresses = [address for address in addresses if isinstance(address, str) and (address == self.account.username or re.fullmatch(r'[A-Za-z0-9_+.-]+@[A-Za-z0-9.-]+', address))]
                self.db.executemany('INSERT OR IGNORE INTO accounts(address) VALUES (?)', [(address,) for address in addresses])
            except core.CleanupError as exc:
                self._warn(str(exc))
            self.meta['accounts_loaded'] = True

    def _mail(self):
        self._accounts()
        # One account's UAPI request per batch keeps slow mail services bounded.
        account = self.db.execute('SELECT * FROM accounts WHERE done=0 ORDER BY address LIMIT 1').fetchone()
        box = self.db.execute("SELECT * FROM boxes WHERE phase!='done' ORDER BY id LIMIT 1").fetchone()
        if box is None and account is not None:
            address = account['address']
            base = 'mail' if address == self.account.username else 'mail/' + '/'.join(reversed(address.rsplit('@', 1)))
            try:
                for row in self.api.call('Mailboxes', 'get_mailbox_status_list', account=address) or []:
                    name = row.get('mailbox', '')
                    short = name[6:] if name.startswith('INBOX.') else name
                    guid, category = row.get('guid', ''), core.MAILBOXES.get(short.lower())
                    if category and int(row.get('messages', 0)) > 0 and re.fullmatch(r'[a-fA-F0-9]{32}', guid):
                        self.db.execute('INSERT INTO boxes(address,path,guid,category) VALUES (?,?,?,?)',
                                        (address, base + '/.' + short, guid, category))
            except (core.CleanupError, OSError, TypeError, ValueError) as exc:
                self._warn('No se pudo inspeccionar el correo de ' + address + ': ' + str(exc))
            self.db.execute('UPDATE accounts SET done=1 WHERE address=?', (address,))
            return
        if box is None:
            self._finish()
            return
        try:
            if box['phase'] == 'index':
                self._mail_index(box)
            else:
                self._mail_files(box)
        except (OSError, core.CleanupError, ValueError) as exc:
            self._warn('Correo de ' + box['address'] + ' · ' + box['path'].rsplit('/', 1)[-1] + ': ' + str(exc))
            self.db.execute("UPDATE boxes SET phase='done' WHERE id=?", (box['id'],))
            self.db.execute('DELETE FROM uids WHERE task=?', (box['id'],))

    def _mail_index(self, box):
        with core.open_parent(self.account, box['path'] + '/dovecot-uidlist') as (fd, name):
            opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(opened, 'r', encoding='utf-8', errors='surrogateescape') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise core.CleanupError('Índice de correo inválido.')
                header = stream.readline(4096).split()
                guid = next((part[1:] for part in header if part.startswith('G')), '')
                validity = next((part[1:] for part in header if part.startswith('V')), '')
                if not header or header[0] != '3' or guid.lower() != box['guid'].lower() or not validity.isdigit():
                    raise core.CleanupError('El índice Maildir y la carpeta no coinciden.')
                signature = json.dumps(core.fingerprint(info))
                if box['signature'] and box['signature'] != signature:
                    if box['restarts'] >= 2:
                        raise core.CleanupError('El índice cambió varias veces durante el análisis. Vuelve a revisar este buzón.')
                    self.db.execute('DELETE FROM uids WHERE task=?', (box['id'],))
                    self.db.execute('UPDATE boxes SET pos=0,signature=NULL,restarts=restarts+1 WHERE id=?', (box['id'],))
                    return
                if box['pos']:
                    stream.seek(box['pos'])
                self.db.execute('UPDATE boxes SET validity=?,signature=? WHERE id=?', (validity, signature, box['id']))
                while not self._limited():
                    line = stream.readline(65536)
                    if not line:
                        self.db.execute("UPDATE boxes SET phase='cur',after_name=NULL WHERE id=?", (box['id'],))
                        return
                    self.processed += 1
                    self.meta['mail_visited'] += 1
                    if len(line) >= 65536:
                        raise core.CleanupError('Entrada de índice demasiado larga.')
                    prefix, separator, stored = line.rstrip('\n').partition(':')
                    uid = prefix.split(' ', 1)[0]
                    base = stored.split(':2,', 1)[0]
                    if separator and uid.isdigit() and 0 < int(uid) < 2**32 and base and '/' not in base and '\\' not in base:
                        self.db.execute('INSERT INTO uids VALUES (?,?,?) ON CONFLICT(task,base) DO UPDATE SET uid=NULL WHERE uid!=excluded.uid', (box['id'], json.dumps(base), int(uid)))
                    self.db.execute('UPDATE boxes SET pos=? WHERE id=?', (stream.tell(), box['id']))

    def _mail_files(self, box):
        relative = box['path'] + '/' + box['phase']
        next_phase = 'new' if box['phase'] == 'cur' else 'done'
        try:
            with core.open_parent(self.account, relative + '/.cct-anchor') as (fd, unused):
                with os.scandir(fd) as stream:
                    entries = sorted(stream, key=lambda entry: entry.name)
                start = bisect.bisect_right([entry.name for entry in entries], json.loads(box['after_name'])) if box['after_name'] is not None else 0
                for entry in entries[start:]:
                    if self._limited():
                        return
                    self.processed += 1
                    self.meta['mail_visited'] += 1
                    self.db.execute('UPDATE boxes SET after_name=? WHERE id=?', (json.dumps(entry.name), box['id']))
                    uid = self.db.execute('SELECT uid FROM uids WHERE task=? AND base=?', (box['id'], json.dumps(entry.name.split(':2,', 1)[0]))).fetchone()
                    if uid is None or uid['uid'] is None:
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        self._item(core.make_item(self.account, relative + '/' + entry.name, info,
                            box['category'], 'review', 'Mensaje en spam o papelera. Se elimina por su UID mediante cPanel.',
                            account=box['address'], mailbox_guid=box['guid'], uid=uid['uid'],
                            uid_validity=box['validity'], mailbox=None), box['id'])
        except FileNotFoundError:
            pass
        self.db.execute('UPDATE boxes SET phase=?,after_name=NULL WHERE id=?', (next_phase, box['id']))
        if next_phase == 'done':
            self.db.execute('DELETE FROM uids WHERE task=?', (box['id'],))

    def _finish(self):
        backups = [json.loads(row['data']) for row in self.db.execute("SELECT data FROM items WHERE category='backups'")]
        retain_backups(backups, self.meta['partial'])
        self.db.executemany('UPDATE items SET data=? WHERE id=?', [(json.dumps(item), item['id']) for item in backups])
        self.meta['phase'] = 'done'

    def step(self, token):
        self._check(token)
        self.deadline = time.monotonic() + self.seconds
        self.processed = 0
        if self.meta['phase'] == 'files':
            self._files()
        elif self.meta['phase'] == 'mail':
            self._mail()
        self.meta['batches'] += 1
        self._save()
        return self.status()

    def results(self, token, offset=0):
        self._check(token)
        if self.meta['phase'] != 'done':
            raise core.CleanupError('Completa el análisis antes de revisar los resultados.')
        if type(offset) is not int or not 0 <= offset <= MAX_RESULTS:
            raise core.CleanupError('Página de resultados inválida.')
        rows = self.db.execute('SELECT data FROM items ORDER BY id LIMIT ? OFFSET ?', (PAGE_SIZE, offset)).fetchall()
        following = offset + len(rows)
        return {'items': [json.loads(row['data']) for row in rows],
                'next_offset': following if following < self.meta['candidates'] else None,
                'scan': self.status()}

    def approved(self, token, requested):
        self._check(token)
        if self.meta['phase'] != 'done':
            raise core.CleanupError('Completa el análisis antes de eliminar.')
        if not isinstance(requested, list) or not 1 <= len(requested) <= core.MAX_DELETE:
            raise core.CleanupError('Selección inválida.')
        items = []
        for row in requested:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                raise core.CleanupError('Selección inválida.')
            saved = self.db.execute('SELECT data FROM items WHERE id=?', (row['id'],)).fetchone()
            if saved is None or not core.same_item(row, json.loads(saved['data'])):
                raise core.CleanupError('La selección no coincide con el análisis. Vuelve a analizar.')
            item = json.loads(saved['data'])
            if not item['selectable']:
                raise core.CleanupError(item['reason'])
            items.append(item)
        return items


def recheck_selection(account, api, approved):
    """Revalidate selected paths/UIDs without restarting the full home traversal."""
    current, warnings, backups, mail = [], [], set(), defaultdict(list)
    for item in approved:
        if item.get('account'):
            mail[(item['account'], item['mailbox_guid'], item['uid_validity'])].append(item)
            continue
        try:
            info = core.safe_stat(account, item['relative_path'])
            match = core.classify(item['relative_path'], info, time.time())
            if not stat.S_ISREG(info.st_mode) or match is None:
                continue
            fresh = core.make_item(account, item['relative_path'], info, *match)
            current.append(fresh)
            if fresh['category'] == 'backups':
                backups.add(str(PurePosixPath(item['relative_path']).parent))
        except (OSError, core.CleanupError):
            continue
    for parent in backups:
        group = []
        started = time.monotonic()
        try:
            with core.open_parent(account, (parent + '/' if parent != '.' else '') + '.cct-anchor') as (fd, unused):
                with os.scandir(fd) as stream:
                    for entry in stream:
                        if time.monotonic() - started > 5:
                            raise core.CleanupError('No se pudo comprobar la retención del respaldo en este lote.')
                        relative = (parent + '/' if parent != '.' else '') + entry.name
                        if not possible_file(relative):
                            continue
                        info = entry.stat(follow_symlinks=False)
                        match = core.classify(relative, info, time.time())
                        if stat.S_ISREG(info.st_mode) and match and match[0] == 'backups':
                            group.append(core.make_item(account, relative, info, *match))
            retain_backups(group)
            by_id = {item['id']: item for item in group}
            current = [by_id.get(item['id'], item) if str(PurePosixPath(item['relative_path']).parent) == parent else item for item in current]
        except (OSError, core.CleanupError) as exc:
            warnings.append(str(exc))
            current = [item for item in current if str(PurePosixPath(item['relative_path']).parent) != parent]
    if mail:
        owned = {account.username} | {row['email'] for row in api.call('Email', 'list_pops') or []}
        statuses = {}
        for (address, guid, validity), rows in mail.items():
            try:
                if address not in owned:
                    raise core.CleanupError('La cuenta de correo ya no pertenece a este usuario.')
                if address not in statuses:
                    statuses[address] = api.call('Mailboxes', 'get_mailbox_status_list', account=address) or []
                if not any(row.get('guid', '').lower() == guid.lower() for row in statuses[address]):
                    raise core.CleanupError('La carpeta de correo cambió. Vuelve a analizar.')
                parent = str(PurePosixPath(rows[0]['relative_path']).parent.parent)
                wanted = {item['uid'] for item in rows}
                names = {}
                started = time.monotonic()
                with core.open_parent(account, parent + '/dovecot-uidlist') as (fd, name):
                    opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    with os.fdopen(opened, 'r', encoding='utf-8', errors='surrogateescape') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise core.CleanupError('Índice de correo inválido.')
                        header = stream.readline(4096).split()
                        file_guid = next((value[1:] for value in header if value.startswith('G')), '')
                        if not header or header[0] != '3' or file_guid.lower() != guid.lower() or 'V' + validity not in header:
                            raise core.CleanupError('La identidad del buzón cambió. Vuelve a analizar.')
                        while wanted - names.keys():
                            if time.monotonic() - started > 8:
                                raise core.CleanupError('La validación del índice de correo agotó su tiempo.')
                            line = stream.readline(65536)
                            if not line:
                                break
                            if len(line) >= 65536:
                                raise core.CleanupError('Entrada de índice demasiado larga.')
                            prefix, separator, stored = line.rstrip('\n').partition(':')
                            uid = prefix.split(' ', 1)[0]
                            if separator and uid.isdigit() and int(uid) in wanted:
                                names[int(uid)] = stored.split(':2,', 1)[0]
                for item in rows:
                    if names.get(item['uid']) != item['name'].split(':2,', 1)[0]:
                        continue
                    info = core.safe_stat(account, item['relative_path'])
                    if stat.S_ISREG(info.st_mode):
                        current.append(core.make_item(account, item['relative_path'], info, item['category'], 'review', item['reason'],
                            account=address, mailbox_guid=guid, uid=item['uid'], uid_validity=validity, mailbox=item.get('mailbox')))
            except (OSError, core.CleanupError, ValueError) as exc:
                warnings.append(str(exc))
    return {'items': current, 'partial': False, 'warnings': warnings}
