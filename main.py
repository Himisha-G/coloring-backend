import os
import json
import uuid
import random
import asyncio
from collections import deque
from typing import Dict, Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware


# ============================================================
# APP CONFIGURATION
# ============================================================

app = FastAPI(
    title="Coloring Service",
    description="WebSocket service for two-person collaborative coloring",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# CONFIG
# ============================================================

# Keep this list in sync with your frontend DRAWINGS array.
IMAGE_IDS = ["a1", "a2", "a3", "a4", "a5", "a6"]


# ============================================================
# STATE
# ============================================================

# active_users[websocket] = {
#     "id": "abc123",
#     "room_id": "room_xxxx" | None,
# }
active_users: Dict[WebSocket, Dict] = {}

# rooms[room_id] = {
#     "image_id": "a3",
#     "left": websocket | None,
#     "right": websocket | None,
#     "history": [ {type, ...}, ... ],   # for reconnect / late join
# }
rooms: Dict[str, Dict] = {}

waiting_users = deque()
match_lock = asyncio.Lock()


# ============================================================
# HELPERS
# ============================================================

async def send_json(websocket: WebSocket, data: dict):
    try:
        await websocket.send_json(data)
        return True
    except Exception as e:
        print(f"❌ Failed to send message: {e}")
        return False


def remove_from_waiting_queue(websocket: WebSocket):
    try:
        while websocket in waiting_users:
            waiting_users.remove(websocket)
    except ValueError:
        pass


def other_side(side: str) -> str:
    return "right" if side == "left" else "left"


def get_room_for(websocket: WebSocket) -> Optional[Dict]:
    user = active_users.get(websocket)
    if not user or not user.get("room_id"):
        return None
    return rooms.get(user["room_id"])


def get_side_for(websocket: WebSocket, room: Dict) -> Optional[str]:
    if room.get("left") is websocket:
        return "left"
    if room.get("right") is websocket:
        return "right"
    return None


async def add_to_waiting_queue(websocket: WebSocket):
    if websocket not in active_users:
        return

    remove_from_waiting_queue(websocket)
    active_users[websocket]["room_id"] = None
    waiting_users.append(websocket)

    await send_json(
        websocket,
        {"type": "waiting", "message": "Waiting for a coloring partner..."},
    )


async def create_room_and_match(websocket: WebSocket):
    """
    Try to pair this socket with someone else waiting.
    If no one is waiting, put this socket in the queue.
    """
    async with match_lock:

        if websocket not in active_users:
            return

        remove_from_waiting_queue(websocket)

        partner: Optional[WebSocket] = None

        while waiting_users:
            candidate = waiting_users.popleft()

            if candidate not in active_users:
                continue
            if candidate is websocket:
                continue
            if active_users[candidate].get("room_id"):
                continue

            partner = candidate
            break

        if partner is None:
            waiting_users.append(websocket)
            await send_json(
                websocket,
                {"type": "waiting", "message": "Waiting for a coloring partner..."},
            )
            print(f"🕒 {active_users[websocket]['id']} waiting for a coloring partner")
            return

        # ------------------------------------------------------
        # Create the room
        # ------------------------------------------------------

        room_id = str(uuid.uuid4())[:8]
        image_id = random.choice(IMAGE_IDS)

        # Randomize who gets which side so it's not always
        # "whoever connected first = left".
        sides = ["left", "right"]
        random.shuffle(sides)
        first_side, second_side = sides

        rooms[room_id] = {
            "image_id": image_id,
            "left": partner if first_side == "left" else websocket,
            "right": websocket if first_side == "left" else partner,
            "history": [],
        }

        active_users[partner]["room_id"] = room_id
        active_users[websocket]["room_id"] = room_id

        partner_side = get_side_for(partner, rooms[room_id])
        my_side = get_side_for(websocket, rooms[room_id])

        print(
            f"🤝 Room {room_id}: image={image_id} "
            f"{active_users[partner]['id']}={partner_side} "
            f"{active_users[websocket]['id']}={my_side}"
        )

        await send_json(
            partner,
            {
                "type": "matched",
                "room_id": room_id,
                "image_id": image_id,
                "side": partner_side,
            },
        )

        await send_json(
            websocket,
            {
                "type": "matched",
                "room_id": room_id,
                "image_id": image_id,
                "side": my_side,
            },
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
async def health_check():
    return {"status": "ok", "service": "Coloring Service", "websocket": "/ws/coloring"}


# ============================================================
# WEBSOCKET ENDPOINT
# ============================================================

@app.websocket("/ws/coloring")
async def coloring_endpoint(websocket: WebSocket):

    client_id = str(uuid.uuid4())[:8]

    try:
        await websocket.accept()
        print(f"✅ Coloring WS connected: {client_id}")

        active_users[websocket] = {"id": client_id, "room_id": None}

        await send_json(
            websocket,
            {"type": "connected", "client_id": client_id},
        )

        await create_room_and_match(websocket)

        while True:
            try:
                raw_data = await websocket.receive_text()

                try:
                    data = json.loads(raw_data)
                except json.JSONDecodeError:
                    continue

                message_type = data.get("type")
                room = get_room_for(websocket)

                # --------------------------------------------
                # DRAW EVENTS: fill / stroke / eraser / reset
                # --------------------------------------------
                if message_type in ("fill", "stroke", "eraser", "reset"):

                    if not room:
                        await send_json(
                            websocket,
                            {"type": "error", "message": "You're not in a room yet."},
                        )
                        continue

                    my_side = get_side_for(websocket, room)

                    event = {
                        "type": message_type,
                        "side": my_side,
                        "x": data.get("x"),
                        "y": data.get("y"),
                        "color": data.get("color"),
                        "size": data.get("size"),
                        "points": data.get("points"),
                    }

                    # Keep last 500 events for anyone reconnecting.
                    if message_type == "reset":
                        room["history"] = []
                    else:
                        room["history"].append(event)
                        room["history"] = room["history"][-500:]

                    partner = room["right"] if room["left"] is websocket else room["left"]

                    if partner and partner in active_users:
                        await send_json(partner, event)

                # --------------------------------------------
                # LEAVE ROOM (voluntary)
                # --------------------------------------------
                elif message_type == "leave":

                    if room:
                        room_id = active_users[websocket]["room_id"]
                        partner = room["right"] if room["left"] is websocket else room["left"]

                        if partner and partner in active_users:
                            await send_json(
                                partner,
                                {"type": "partner_left", "message": "Your partner left."},
                            )
                            active_users[partner]["room_id"] = None
                            await add_to_waiting_queue(partner)
                            await create_room_and_match(partner)

                        rooms.pop(room_id, None)

                    active_users[websocket]["room_id"] = None
                    await add_to_waiting_queue(websocket)
                    await create_room_and_match(websocket)

                else:
                    await send_json(
                        websocket,
                        {"type": "error", "message": f"Unknown event type: {message_type}"},
                    )

            except WebSocketDisconnect:
                print(f"🔌 Coloring WS disconnected: {client_id}")
                break
            except Exception as e:
                print(f"❌ Message handling error for {client_id}: {e}")
                break

    except WebSocketDisconnect:
        print(f"🔌 Coloring WS disconnected during setup: {client_id}")
    except Exception as e:
        print(f"💥 Critical WS error for {client_id}: {e}")

    finally:
        print(f"🧹 Cleaning up {client_id}")

        user_data = active_users.pop(websocket, None)
        remove_from_waiting_queue(websocket)

        if user_data and user_data.get("room_id"):
            room_id = user_data["room_id"]
            room = rooms.get(room_id)

            if room:
                partner = room["right"] if room["left"] is websocket else room["left"]

                if partner and partner in active_users:
                    await send_json(
                        partner,
                        {
                            "type": "partner_left",
                            "message": "Your partner disconnected. Your artwork is saved.",
                        },
                    )
                    active_users[partner]["room_id"] = None
                    await add_to_waiting_queue(partner)
                    await create_room_and_match(partner)

                # Room state (history) is kept for a bit in memory in case
                # you want to support "resume same room" later. For a first
                # version we just drop it once both sides are gone.
                if not (room.get("left") in active_users or room.get("right") in active_users):
                    rooms.pop(room_id, None)

        print(f"✅ Cleanup complete for {client_id}")


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8081))
    print(f"🚀 Starting Coloring Service on port {port}")
    print(f"📡 WebSocket endpoint: ws://0.0.0.0:{port}/ws/coloring")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        timeout_keep_alive=300,
        timeout_graceful_shutdown=10,
    )
