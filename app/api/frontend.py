"""Frontend router – serves the Vite-built SPA in production, or a simple
dev-mode notice otherwise.
"""

import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, FileResponse

router = APIRouter()

_BUILT_INDEX = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "static", "index.html"
)


@router.get("/", response_class=HTMLResponse)
def index():
    """Serve the built Vite SPA.  If not yet built, show a quick-start hint."""
    if os.path.isfile(_BUILT_INDEX):
        return FileResponse(_BUILT_INDEX, media_type="text/html")
    return HTMLResponse(
        "<h2>Frontend not built</h2>"
        "<p>Run <code>cd app/frontend &amp;&amp; npm run build</code> "
        "or use the Vite dev server on <a href='http://localhost:5173'>localhost:5173</a>.</p>"
    )
