from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import DEMO_MODEL, RECOVERY_DEMO_LOAD_ONLY
from app.database import Base, engine
from app.api import orders, webhooks, admin, dashboard

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.recovery.predictor import artifact_metadata, load, selected_model_path, train_and_load

    if RECOVERY_DEMO_LOAD_ONLY:
        load()
        model_mode = "loaded_existing_artifact"
    elif DEMO_MODEL == "lr":
        load()
        model_mode = "loaded_existing_demo_artifact"
    else:
        train_and_load()
        model_mode = "trained_and_loaded"

    app.state.model_diagnostics = {
        "mode": model_mode,
        "model": DEMO_MODEL,
        **artifact_metadata(selected_model_path()),
    }
    print(f"[predictor] startup {app.state.model_diagnostics}")
    # Start scheduler
    from app.scheduler import start
    scheduler = start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Revenue Recovery Intelligence Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders.router)
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(dashboard.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/diagnostics/model")
def model_diagnostics():
    """Expose the exact model artifact identity used by this API process."""
    return app.state.model_diagnostics
