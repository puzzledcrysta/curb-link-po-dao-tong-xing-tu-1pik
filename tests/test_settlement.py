import base64
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.errors import DataError
from app.modules.reconcile.ledger import ledger, read_book
from app.settings import KEY, UNIT


def b64(payload):
    return base64.b64encode(payload).decode()


def post(client, path, body):
    return client.post(path, json=body)


def make_book(rows):
    return (f'{KEY},外部记录值({UNIT})\n' + ''.join(f'{key},{value}\n' for key, value in rows)).encode()


@pytest.fixture
def settled(client, source, book):
    report = post(client, '/api/settlements', {'data_base64': b64(source), 'book_base64': b64(book)}).json()
    return report


def test_settles_approved_mileage_without_waiting_for_disputes(settled):
    assert settled['counts']['approved'] == 26
    assert settled['counts']['settled'] == 25
    assert settled['counts']['pending'] == 1
    assert settled['outcome']['posted'] == 25


def test_pending_order_is_visible_with_reason(settled):
    pending = [row for row in settled['rows'] if row['state'] == '欠凭据']
    assert [(row['key'], row['code']) for row in pending] == [('00103', 'MISSING_BOOK')]


def test_book_key_without_approved_result_is_unapplied(settled):
    assert [row['key'] for row in settled['unapplied']] == ['09999']
    assert settled['counts']['unapplied'] == 1


def test_settled_rows_keep_mileage_reference_and_evidence(settled):
    row = next(row for row in settled['rows'] if row['key'] == '00101')
    assert (row['state'], row['amount'], row['reference']) == ('已结', '120', '120')
    assert row['raw'] and row['proof'] and row['settled']
    assert row['evidence'] and row['evidence'][0]['value'] == '120'


def test_totals_only_cover_posted_rows(settled):
    assert settled['totals'] == {'settled': '2995', 'reference': '2996', 'delta': '-1'}


def test_duplicate_numbers_are_disputed_not_posted(settled, client):
    batch_id = settled['batch']['batch_id']
    extra = f'{KEY},外部记录值({UNIT})\n00103,110\n00103,999\n'.encode()
    report = post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(extra)}).json()
    row = next(row for row in report['rows'] if row['key'] == '00103')
    assert (row['state'], row['code']) == ('欠凭据', 'BOOK_CONFLICT')
    assert report['counts']['settled'] == 25
    assert report['counts']['disputed'] == 1


def test_duplicate_same_value_is_agreement(settled, client):
    batch_id = settled['batch']['batch_id']
    extra = f'{KEY},外部记录值({UNIT})\n00103,110\n00103,110\n'.encode()
    report = post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(extra)}).json()
    assert report['counts']['settled'] == 26 and report['counts']['pending'] == 0
    assert next(row for row in report['rows'] if row['key'] == '00103')['code'] == 'MATCH'


def test_supplement_clarifies_dispute(settled, client):
    batch_id = settled['batch']['batch_id']
    dispute = f'{KEY},外部记录值({UNIT})\n00103,120\n00103,999\n'.encode()
    post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(dispute)})
    clarify = f'{KEY},外部记录值({UNIT})\n00103,110\n'.encode()
    report = post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(clarify)}).json()
    row = next(row for row in report['rows'] if row['key'] == '00103')
    assert (row['state'], row['code'], row['delta']) == ('已结', 'MATCH', '0')
    assert report['counts'] == {**report['counts'], 'settled': 26, 'pending': 0, 'disputed': 0}


def test_already_settled_row_not_reposted_by_later_book(settled, client):
    batch_id = settled['batch']['batch_id']
    wrong = f'{KEY},外部记录值({UNIT})\n00101,1\n'.encode()
    report = post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(wrong)}).json()
    row = next(row for row in report['rows'] if row['key'] == '00101')
    assert (row['state'], row['amount'], row['reference']) == ('已结', '120', '120')
    assert report['outcome']['posted'] == 0


def test_supplement_retry_is_idempotent(settled, client):
    batch_id = settled['batch']['batch_id']
    extra = f'{KEY},外部记录值({UNIT})\n00103,110\n'.encode()
    first = post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(extra)}).json()
    second = post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(extra)}).json()
    assert first['outcome']['new_attempt'] is True and first['outcome']['posted'] == 1
    assert second['outcome']['new_attempt'] is False and second['outcome']['posted'] == 0
    assert second['counts']['settled'] == 26


def test_initial_settlement_retry_does_not_double_book(settled, client, source, book):
    again = post(client, '/api/settlements', {'data_base64': b64(source), 'book_base64': b64(book)}).json()
    assert again['outcome']['new_attempt'] is False and again['outcome']['posted'] == 0
    assert again['totals'] == settled['totals']


def test_concurrent_supplements_post_once(client, settled):
    batch_id = settled['batch']['batch_id']
    extra = f'{KEY},外部记录值({UNIT})\n00103,110\n'.encode()

    def attempt(_):
        return post(client, f'/api/settlements/{batch_id}/supplement', {'book_base64': b64(extra)}).json()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sum(result['outcome']['posted'] for result in results) == 1
    assert all(result['counts']['settled'] == 26 for result in results)


def test_hung_batch_without_deferred_book_still_settles(client, source):
    partial = f'{KEY},外部记录值({UNIT})\n00101,120\n'.encode()
    report = post(client, '/api/settlements', {'data_base64': b64(source), 'book_base64': b64(partial)}).json()
    assert report['counts']['settled'] == 1
    assert report['counts']['pending'] == 25


def test_unknown_batch(client):
    problem = client.get('/api/settlements/not-a-batch')
    assert (problem.status_code, problem.json()['code']) == (404, 'SETTLEMENT_UNKNOWN')


def test_supplement_rejects_bad_book(client, settled):
    batch_id = settled['batch']['batch_id']
    problem = post(client, f'/api/settlements/{batch_id}/supplement', {'book': 'bad,header\n1,2\n'})
    assert (problem.status_code, problem.json()['code']) == (422, 'HEADER')


def test_repeated_key_kept_by_tolerant_reader(source, book):
    raw = book + b'00101,121\n'
    assert len(read_book(raw)['00101']) == 2
    with pytest.raises(DataError) as error:
        ledger(raw)
    assert error.value.code == 'BOOK_CONFLICT'


def test_export_contains_both_settled_and_pending(client, settled):
    batch_id = settled['batch']['batch_id']
    payload = client.get(f'/api/settlements/{batch_id}/export')
    assert payload.status_code == 200 and 'attachment' in payload.headers['content-disposition']
    body = payload.json()
    assert [row['key'] for row in body['pending']] == ['00103']
    assert body['settled'] and body['attempts']
