---
paths:
  - "**/*.py"
---
# FastAPI Patterns

> Patterns from ECC fastapi-patterns skill. Applicable to `utils/local_ui.py`.

## Application Factory

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

def create_app() -> FastAPI:
    app = FastAPI(
        title="Video.AI API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(pipeline.router, prefix="/api/v1/pipeline", tags=["pipeline"])
    return app

app = create_app()
```

## Dependency Injection

```python
from fastapi import Depends, HTTPException, status

async def get_db():
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(token)
    user_id = UUID(payload["sub"])
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user
```

## Pydantic Schemas

```python
from pydantic import BaseModel, ConfigDict, Field

class PipelineRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    duration: int = Field(ge=1, le=60, default=10)

class PipelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    output_path: str | None = None
```

## Error Handling

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message

def register_exception_handlers(app: FastAPI):
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
```

## Testing with Dependency Overrides

```python
from httpx import ASGITransport, AsyncClient

@pytest.fixture
async def client(test_session):
    app = create_app()

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

## Security Checklist

- [ ] CORS origins environment-specific
- [ ] Rate limits on auth endpoints
- [ ] Pydantic models for all request bodies
- [ ] ORM parameter binding, no f-string SQL
- [ ] Tokens/headers redacted from logs
