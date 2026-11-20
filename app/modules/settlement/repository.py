import json
from datetime import datetime, timezone

from app.errors import DataError
from app.store import connection


def now():
    return datetime.now(timezone.utc).isoformat()


def _batch(db,batch_id):
    row = db.execute('SELECT * FROM settlement_batches WHERE batch_id=?',(batch_id,)).fetchone()
    if row is None:
        raise DataError('SETTLEMENT_UNKNOWN','没有此结算批次',404)
    return row


def batch(batch_id):
    with connection() as db:
        return dict(_batch(db,batch_id))


def batch_exists(batch_id):
    with connection() as db:
        return db.execute('SELECT 1 FROM settlement_batches WHERE batch_id=?',(batch_id,)).fetchone() is not None


def attempts(batch_id):
    with connection() as db:
        _batch(db,batch_id)
        return [dict(row) for row in db.execute(
            'SELECT id,batch_id,book_sha256,kind,settled,created FROM settlement_attempts '
            'WHERE batch_id=? ORDER BY id',(batch_id,))]


def batches():
    with connection() as db:
        return [dict(row) for row in db.execute(
            'SELECT batch_id,source_sha256,book_sha256,created,updated FROM settlement_batches '
            'ORDER BY updated DESC,batch_id DESC LIMIT 50')]


def evidence(batch_id):
    with connection() as db:
        _batch(db,batch_id)
        grouped = {}
        for row in db.execute('SELECT key,value,line,attempt_id FROM settlement_evidence '
                              'WHERE batch_id=? ORDER BY attempt_id,line,value',(batch_id,)):
            grouped.setdefault(row['key'],[]).append(
                {'value':row['value'],'line':row['line'],'attempt_id':row['attempt_id']})
        return grouped


def settled_rows(batch_id):
    with connection() as db:
        _batch(db,batch_id)
        return [dict(row) for row in db.execute(
            'SELECT * FROM settlements WHERE batch_id=? ORDER BY key',(batch_id,))]


def commit(batch_id,source_hash,book_hash,source,book,entries,kind,settler):
    """Record one external-book attempt and post the rows it newly settles.

    The attempt is unique per (batch, book) — retrying the identical supplement
    is a no-op and can never post the same mileage twice.
    """
    stamp = now()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO settlement_batches '
                   '(batch_id,source_sha256,book_sha256,source,book,created,updated) VALUES (?,?,?,?,?,?,?)',
                   (batch_id,source_hash,book_hash,source,book,stamp,stamp))
        db.execute('UPDATE settlement_batches SET updated=? WHERE batch_id=?',(stamp,batch_id))
        cursor = db.execute('INSERT OR IGNORE INTO settlement_attempts '
                            '(batch_id,book_sha256,kind,book,settled,created) VALUES (?,?,?,?,0,?)',
                            (batch_id,book_hash,kind,book,stamp))
        is_new = bool(cursor.rowcount)
        attempt = db.execute('SELECT * FROM settlement_attempts WHERE batch_id=? AND book_sha256=?',
                             (batch_id,book_hash)).fetchone()
        posted = 0
        if is_new:
            for key,items in entries.items():
                for value,line in items:
                    db.execute('INSERT INTO settlement_evidence (batch_id,key,value,line,attempt_id) '
                               'VALUES (?,?,?,?,?)',(batch_id,key,value,line,attempt['id']))
            existing = {row['key'] for row in db.execute(
                'SELECT key FROM settlements WHERE batch_id=?',(batch_id,))}
            for row in settler(attempt['id'],existing):
                posted += db.execute(
                    'INSERT OR IGNORE INTO settlements '
                    '(batch_id,key,amount,reference,reference_line,delta,code,source_line,revision,raw,proof,attempt_id,settled) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (batch_id,row['key'],row['amount'],row['reference'],row['reference_line'],
                     row['delta'],row['code'],row['source_line'],row['revision'],
                     json.dumps(row['raw'],ensure_ascii=False),
                     json.dumps(row['proof'],ensure_ascii=False),attempt['id'],stamp)).rowcount
            db.execute('UPDATE settlement_attempts SET settled=? WHERE id=?',(posted,attempt['id']))
        return {'attempt':dict(attempt),'new':is_new,'posted':posted}
