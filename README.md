# coloring-backend

WebSocket service that pairs two people into a room and syncs a collaborative,
vertically-split coloring session between them. Kept completely separate from
the anonymous-chat backend (`mannbackend`) — this service knows nothing about
bots, Gemini, or LangGraph.

## What it does

- Accepts a WebSocket connection at `/ws/coloring`.
- Puts each new connection in a waiting queue.
- When two people are waiting, creates a room: picks a shared image id and
  randomly assigns each person `"left"` or `"right"`.
- Forwards `fill` / `stroke` / `eraser` / `reset` events from one side to the
  other — it never re-broadcasts the whole canvas, only the small event.
- On disconnect, tells the remaining partner and puts them back in the queue
  to be matched with someone new.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

The server starts on `http://localhost:8081` with the WebSocket endpoint at
`ws://localhost:8081/ws/coloring`.

Open two browser tabs pointed at your frontend (with
`VITE_COLORING_WS_URL=ws://localhost:8081/ws/coloring`) to test a full
two-person session.

## Deploy on Render

1. Push this repo to GitHub.
2. On Render: **New → Web Service**, connect the repo.
3. Render will pick up `render.yaml` automatically (or set manually):
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Once deployed, your endpoint is:
   `wss://<your-service-name>.onrender.com/ws/coloring`
5. Set that as `VITE_COLORING_WS_URL` in your frontend's environment.

## Protocol

**Server → client**

| type | fields |
|---|---|
| `connected` | `client_id` |
| `waiting` | `message` |
| `matched` | `room_id`, `image_id`, `side` (`"left"` \| `"right"`) |
| `partner_left` | `message` |
| `fill` / `stroke` / `eraser` / `reset` | forwarded draw event, includes `side` |

**Client → server**

| type | fields |
|---|---|
| `fill` | `x`, `y`, `color` |
| `stroke` | `points`, `color`, `size` |
| `eraser` | `x`, `y` |
| `reset` | — |
| `leave` | — |

## Image IDs

The `IMAGE_IDS` list in `main.py` must match the image ids used in the
frontend's `DRAWINGS` array (`a1`–`a6`). Update both together if you add or
remove outline images.

## Notes / next steps

- Room `history` is kept in memory only, for the lifetime of the process. If
  you need drawings to survive a server restart, swap that for Redis or a
  small database.
- CORS is currently wide open (`allow_origins=["*"]`) for ease of development.
  Lock this down to your actual frontend origin before shipping publicly.
