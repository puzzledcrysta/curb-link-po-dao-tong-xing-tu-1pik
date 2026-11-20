from app.errors import DataError
from app.settings import KEY, UNIT
from app.modules.domain.values import number
from app.modules.intake.reader import decode, records


def _rows(payload):
    text, _ = decode(payload)
    rows = iter(records(text))
    _, header = next(rows, (0, []))
    if header != [KEY, f'外部记录值({UNIT})']:
        raise DataError('HEADER','外部账表头或计量单位不符')
    for line, row in rows:
        if not row or not any(value.strip() for value in row):
            continue
        if len(row) != 2 or not row[0].strip():
            raise DataError('WIDTH',f'外部账第 {line} 行缺少业务键或计量')
        yield row[0].strip(), number(row[1]), line


def ledger(payload):
    """Strict external ledger: a repeated key makes the book unusable."""
    by_key = {}
    for key,value,line in _rows(payload):
        if key in by_key:
            raise DataError('BOOK_CONFLICT',f'外部账第 {line} 行重复业务键 {key}')
        by_key[key] = (value,line)
    return by_key


def read_book(payload):
    """Tolerant external ledger: keep every entry so a repeated key can be
    quarantined as an unresolved dispute instead of blocking the batch."""
    by_key = {}
    for key,value,line in _rows(payload):
        by_key.setdefault(key,[]).append((value,line))
    return by_key
