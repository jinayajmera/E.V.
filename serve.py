"""Production-style server (Waitress, works on Windows / Linux / macOS):   python serve.py
Then open http://127.0.0.1:8000   (set PORT / HOST in .env to change)."""
import os
from waitress import serve
from app import create_app

app = create_app()

if __name__ == "__main__":
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8000"))
    print(f"Serving on http://{host}:{port}  (Ctrl+C to stop)")
    serve(app, host=host, port=port, threads=8, max_request_body_size=5 * 1024 * 1024)
