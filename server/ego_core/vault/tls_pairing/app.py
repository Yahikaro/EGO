"""Ephemeral test endpoint. Issuance, approval and revoke are local methods only."""
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from server.ego_core.vault.tls_pairing.pairing import PairingFailure


class Redeem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticket: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=32)


class Claim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pending: str = Field(min_length=1, max_length=128)
    secret: str = Field(min_length=1, max_length=128)


def create_app(store):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.request_count = 0

    @app.middleware('http')
    async def count(request, call_next):
        app.state.request_count += 1
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Framework default includes the invalid input, which can be a secret.
        return JSONResponse(status_code=422, content={'code': 'INVALID_REQUEST'})

    @app.exception_handler(PairingFailure)
    async def pairing_failure(request, exc):
        return JSONResponse(status_code=403, content={'code': str(exc)})

    @app.get('/health')
    def health():
        return {'status': 'ok', 'scope': 'M0 ephemeral spike'}

    @app.post('/pairing/redeem')
    def redeem(body: Redeem):
        pending, secret = store.redeem(body.ticket, body.code)
        return {'pending': pending, 'secret': secret}

    @app.post('/pairing/claim')
    def claim(body: Claim):
        device, token = store.claim(body.pending, body.secret)
        return {'device': device, 'token': token}

    @app.get('/protected')
    def protected(request: Request):
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer ') or len(header) > 256:
            raise HTTPException(401, 'UNAUTHORIZED')
        try:
            device = store.authenticate(header[7:])
        except PairingFailure:
            raise HTTPException(401, 'UNAUTHORIZED') from None
        return {'device': device}

    return app
