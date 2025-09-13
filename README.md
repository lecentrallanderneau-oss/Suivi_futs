# Flask Starter for Render (gunicorn app:app)

Minimal Flask app using SQLAlchemy and Flask-Migrate, ready for Render.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # and adjust if needed
flask db upgrade      # optional, no migrations yet
python app.py
```

## Deploy on Render
- Set the start command to: `gunicorn app:app`
- Add a PostgreSQL instance and set the `DATABASE_URL` env var automatically.
