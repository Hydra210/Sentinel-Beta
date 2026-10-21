"""Compatibility entrypoint for Sentinel's secure backend implementation."""

from app import app


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "3000")))
