from hashlib import sha256
from app.settings import RULE_VERSION


def batch_id(source_hash):
    return sha256(f'{RULE_VERSION}:batch:{source_hash}'.encode()).hexdigest()


def manifest(raw,book):
    source_hash,book_hash = sha256(raw).hexdigest(),sha256(book).hexdigest()
    run_id = sha256(f'{RULE_VERSION}:{source_hash}:{book_hash}'.encode()).hexdigest()
    return {'run_id':run_id,'source_sha256':source_hash,'book_sha256':book_hash,
            'batch_id':batch_id(source_hash),'version':RULE_VERSION,
            'parameters':{'mode':'独立复算'}}
