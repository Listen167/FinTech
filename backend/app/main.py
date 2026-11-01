from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.v1.router import api_router
from backend.app.core.config import settings
from backend.app.errors import ApplicationError


app = FastAPI(
    title=settings.project_name,
    version=settings.api_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.exception_handler(ApplicationError)
async def handle_application_error(_: Request, error: ApplicationError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"detail": error.public_detail()})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    fields = [
        {
            "field": ".".join(str(part) for part in item["loc"] if part != "body"),
            "message": item["msg"],
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "INVALID_REQUEST",
                "message": "请求参数不完整或格式不正确。",
                "retryable": False,
                "details": {"fields": fields},
            }
        },
    )


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.project_name, "status": "ok"}

