from fastapi import APIRouter, Response
from app.inputs import Paired, Book, OptionalBook
from .service import settle, settle_run, supplement, view, batches as all_batches, export

router = APIRouter()


@router.post('/api/settlements')
def create(source: Paired):
    return settle(source.raw(), source.ledger())


@router.post('/api/settlements/from-run/{run_id}')
def create_from_run(run_id: str, book: OptionalBook):
    return settle_run(run_id, book.ledger())


@router.get('/api/settlements')
def list_batches():
    return all_batches()


@router.get('/api/settlements/{batch_id}')
def detail(batch_id: str):
    return view(batch_id)


@router.post('/api/settlements/{batch_id}/supplement')
def add_supplement(batch_id: str, book: Book):
    return supplement(batch_id, book.ledger())


@router.get('/api/settlements/{batch_id}/export')
def download(batch_id: str):
    payload = export(batch_id)
    return Response(payload, media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="settlement-{batch_id}.json"'})
