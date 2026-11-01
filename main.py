"""CamShaft 2D Matrix & Thread Inspection station — FastAPI backend that also serves the built UI."""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.core import config
from src.core.logger import setup_logging
from src.server.routes import State, get_classifier, router

log = setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_classifier()  # load (or train once) before the first CHECK
    saved = State.settings.get_cameras()
    for role, env_index in (("code", config.CODE_CAMERA_INDEX), ("thread", config.THREAD_CAMERA_INDEX)):
        index = int(env_index) if env_index not in (None, "") else saved.get(role)
        if index is not None:
            ok, message = State.cameras.select(role, index)
            log.info("startup: %s camera %s: %s", role, index, message)
    await State.hercules.apply_config(State.settings.get_hercules())
    hstat = State.hercules.status()
    log.info("startup: Hercules %s", f"listening on {hstat['bind_host']}:{hstat['port']}" if hstat["listening"]
             else ("disabled" if not hstat["enabled"] else f"error: {hstat['last_error']}"))
    log.info("startup: reports -> %s", State.settings.get_report_directory())
    yield
    State.shutting_down = True
    await State.hercules.stop()
    State.cameras.shutdown()


app = FastAPI(title="CamShaft 2D Matrix & Thread Inspection", version="2.0.0", lifespan=lifespan)
app.include_router(router)

if (config.FRONTEND_OUT / "_next").exists():
    app.mount("/_next", StaticFiles(directory=config.FRONTEND_OUT / "_next"), name="next-static")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str = ""):
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    if not (config.FRONTEND_OUT / "index.html").exists():
        raise HTTPException(status_code=503, detail="Frontend not built: cd frontend && npm install && npm run build")
    requested = (config.FRONTEND_OUT / path).resolve()
    if path and requested.is_file() and config.FRONTEND_OUT.resolve() in requested.parents:
        return FileResponse(requested)
    return FileResponse(config.FRONTEND_OUT / "index.html")


def _port_free(host: str, port: int) -> bool:
    import socket
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _open_browser_when_ready(url: str) -> None:
    import threading
    import time
    import urllib.request
    import webbrowser

    def wait_and_open():
        for _ in range(240):  # model load + camera open can take a while
            try:
                urllib.request.urlopen(url + "api/health", timeout=1)
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(0.5)

    threading.Thread(target=wait_and_open, daemon=True).start()


def run_application() -> None:
    url = f"http://{'127.0.0.1' if config.HOST in ('0.0.0.0', '') else config.HOST}:{config.PORT}/"
    if not _port_free(config.HOST, config.PORT):
        log.error("Port %s is already in use - the station is probably already running. Opening %s",
                  config.PORT, url)
        if config.OPEN_BROWSER:
            import webbrowser
            webbrowser.open(url)
        return
    if config.OPEN_BROWSER:
        _open_browser_when_ready(url)
    log.info("Starting station on %s (data folder: %s)", url, config.BASE_DIR)
    uvicorn.run(app, host=config.HOST, port=config.PORT, reload=False, log_config=None, loop="asyncio")


if __name__ == "__main__":
    run_application()
