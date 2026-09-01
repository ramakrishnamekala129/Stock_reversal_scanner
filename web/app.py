import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web.state import dashboard_state

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    dashboard_state.set_event_loop(loop)
    yield


app = FastAPI(
    title="Upstox F&O 5-Minute Intraday Reversal Scanner",
    description="Real-time multi-factor intraday reversal dashboard",
    version="2.0.0",
    lifespan=lifespan,
)

# Mount Static Files & Templates
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Renders the main dashboard HTML template."""
    snapshot = dashboard_state.get_snapshot()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"stats": snapshot["stats"]},
    )


@app.get("/api/snapshot", response_class=JSONResponse)
async def get_snapshot():
    """Returns complete state snapshot in JSON."""
    return dashboard_state.get_snapshot()


@app.get("/api/signals", response_class=JSONResponse)
async def get_signals():
    """Returns list of detected signals."""
    return dashboard_state.signals


@app.get("/api/stats", response_class=JSONResponse)
async def get_stats():
    """Returns current summary statistics."""
    return dashboard_state.get_stats()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Live WebSocket feed streaming price updates and signals."""
    await websocket.accept()
    dashboard_state.register_ws(websocket)
    try:
        # Send initial snapshot immediately upon connection
        await websocket.send_json({
            "type": "INITIAL_SNAPSHOT",
            "data": dashboard_state.get_snapshot(),
        })
        while True:
            # Keep connection alive, listen for ping/client messages
            msg = await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard_state.unregister_ws(websocket)
    except Exception:
        dashboard_state.unregister_ws(websocket)
