import json
from decimal import Decimal
from hashlib import sha256

from app.errors import DataError
from app.manifest import batch_id as make_batch_id
from app.modules.reconcile.ledger import read_book
from app.pipeline import stages
from . import repository


def _approved(source):
    return {row['key']: row for row in stages(source)['accepted']}


def _resolve(key, record, entries):
    """Settle one approved order from the entries this book supplied for it.

    A key reported with two different numbers is a dispute: neither may be
    posted, so the order stays pending until a supplement clarifies it.
    """
    candidates = entries.get(key, [])
    values = {value for value, _ in candidates}
    if not candidates:
        return None
    if len(values) > 1:
        return {'key': key, 'code': 'BOOK_CONFLICT',
                'pending': '外部账给同一踏勘单报了多个数，须补单核清'}
    reference = next(iter(values))
    amount = Decimal(record['amount'])
    delta = amount - reference
    return {'key': key, 'amount': record['amount'], 'reference': str(reference),
            'reference_line': min(line for _, line in candidates), 'delta': str(delta),
            'code': 'AMOUNT_DELTA' if delta else 'MATCH', 'source_line': record['line'],
            'revision': record['revision'], 'raw': record['raw'], 'proof': record['proof']}


def _submit(source, book, batch_id=None):
    source_hash = sha256(source).hexdigest()
    expected = make_batch_id(source_hash)
    if batch_id is not None and batch_id != expected:
        raise DataError('SETTLEMENT_UNKNOWN', '来源与该结算批次不一致', 404)
    approved = _approved(source)
    entries = read_book(book)
    kind = 'supplement' if repository.batch_exists(expected) else 'initial'

    def settler(_attempt_id, existing):
        rows = []
        for key, record in approved.items():
            if key in existing:
                continue
            resolved = _resolve(key, record, entries)
            if resolved is not None and 'amount' in resolved:
                rows.append(resolved)
        return rows

    evidence = {key: [(str(value), line) for value, line in items] for key, items in entries.items()}
    outcome = repository.commit(expected, source_hash, sha256(book).hexdigest(), source, book,
                                evidence, kind, settler)
    report = view(expected)
    report['outcome'] = {'new_attempt': outcome['new'], 'posted': outcome['posted'],
                         'attempt_id': outcome['attempt']['id']}
    return report


def settle(source, book):
    return _submit(source, book)


def settle_run(run_id, book=None):
    from app.modules.runs.repository import fetch
    record = fetch(run_id)
    return _submit(record['source'], record['book'] if book is None else book)


def supplement(batch_id, book):
    record = repository.batch(batch_id)
    return _submit(record['source'], book, batch_id)


def view(batch_id):
    record = repository.batch(batch_id)
    approved = _approved(record['source'])
    posted = {row['key']: row for row in repository.settled_rows(batch_id)}
    evidence = repository.evidence(batch_id)
    attempts = repository.attempts(batch_id)

    def _status(key):
        """Reason an unsettled key remains pending, from the latest attempt
        that actually carried it (a later supplement may omit it)."""
        items = evidence.get(key, [])
        if not items:
            return 'MISSING_BOOK'
        latest = max(item['attempt_id'] for item in items)
        values = {item['value'] for item in items if item['attempt_id'] == latest}
        return 'BOOK_CONFLICT' if len(values) > 1 else 'MISSING_BOOK'

    rows = []
    for key, item in approved.items():
        if key in posted:
            row = posted[key]
            rows.append({'key': key, 'amount': row['amount'], 'reference': row['reference'],
                         'reference_line': row['reference_line'], 'delta': row['delta'],
                         'code': row['code'], 'state': '已结', 'source_line': row['source_line'],
                         'revision': row['revision'], 'raw': json.loads(row['raw']),
                         'proof': json.loads(row['proof']), 'settled': row['settled'],
                         'attempt_id': row['attempt_id'],
                         'evidence': [entry for entry in evidence.get(key, [])
                                      if entry['attempt_id'] == row['attempt_id']]})
            continue
        rows.append({'key': key, 'amount': item['amount'], 'reference': None, 'reference_line': None,
                     'delta': None, 'code': _status(key),
                     'state': '欠凭据', 'source_line': item['line'], 'revision': item['revision'],
                     'raw': item['raw'], 'proof': item['proof'], 'settled': None,
                     'attempt_id': None, 'evidence': evidence.get(key, [])})
    rows.sort(key=lambda row: row['key'])

    unapplied = []
    for key in sorted(set(evidence) - set(approved)):
        items = evidence[key]
        latest = max(item['attempt_id'] for item in items)
        values = sorted({item['value'] for item in items if item['attempt_id'] == latest})
        unapplied.append({'key': key, 'state': '待核', 'code': 'MISSING_RESULT',
                          'reference': values[0] if len(values) == 1 else None,
                          'evidence': items})

    settled_rows = [row for row in rows if row['state'] == '已结']
    settled_total = sum((Decimal(row['amount']) for row in settled_rows), Decimal(0))
    reference_total = sum((Decimal(row['reference']) for row in settled_rows), Decimal(0))
    counts = {'approved': len(rows), 'settled': len(settled_rows),
              'pending': sum(row['state'] == '欠凭据' for row in rows),
              'disputed': sum(row['code'] == 'BOOK_CONFLICT' for row in rows),
              'delta': sum(row['code'] == 'AMOUNT_DELTA' for row in rows),
              'unapplied': len(unapplied)}
    return {'batch': {'batch_id': record['batch_id'], 'source_sha256': record['source_sha256'],
                      'book_sha256': record['book_sha256'], 'created': record['created'],
                      'updated': record['updated']},
            'attempts': attempts, 'counts': counts, 'rows': rows, 'unapplied': unapplied,
            'totals': {'settled': str(settled_total), 'reference': str(reference_total),
                       'delta': str(settled_total - reference_total)}}


def batches():
    return repository.batches()


def export(batch_id):
    report = view(batch_id)
    payload = {'batch': report['batch'], 'counts': report['counts'], 'totals': report['totals'],
               'attempts': report['attempts'],
               'settled': [row for row in report['rows'] if row['state'] == '已结'],
               'pending': [row for row in report['rows'] if row['state'] == '欠凭据'],
               'unapplied': report['unapplied']}
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
