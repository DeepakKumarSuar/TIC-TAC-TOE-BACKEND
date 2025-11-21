import asyncio
import websockets
import json
import logging
import random
import os
from http import HTTPStatus
import mimetypes

logging.basicConfig(level=logging.INFO)

ROOMS = {}
WINNING_CONDITIONS = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8],
    [0, 3, 6], [1, 4, 7], [2, 5, 8],
    [0, 4, 8], [2, 4, 6]
]

# ------------------------------------------------------
# STATIC FILE SERVING
# ------------------------------------------------------

async def serve_static_file(path):
    filename = "index.html" if path == "/" else path.lstrip("/")

    try:
        with open(filename, "rb") as f:
            content = f.read()

        mime, _ = mimetypes.guess_type(filename)
        headers = [
            ("Content-Type", mime or "application/octet-stream"),
            ("Content-Length", str(len(content)))
        ]

        return HTTPStatus.OK, headers, content

    except FileNotFoundError:
        return HTTPStatus.NOT_FOUND, [], b"404 Not Found"

    except Exception:
        return HTTPStatus.INTERNAL_SERVER_ERROR, [], b"500 Internal Server Error"


async def process_request(path, headers):
    static_files = ["/", "/index.html", "/styles.css", "/main.py", "/TTT_Img.jpeg"]
    if path in static_files:
        return await serve_static_file(path)
    return None


# ------------------------------------------------------
# GAME LOGIC
# ------------------------------------------------------

def generate_code():
    while True:
        code = str(random.randint(0, 100))
        if code not in ROOMS:
            return code


async def send(ws, type, data=None):
    msg = {"type": type, "data": data or {}}
    try:
        await ws.send(json.dumps(msg))
    except websockets.ConnectionClosed:
        pass


async def broadcast(code, type, data=None):
    if code not in ROOMS:
        return

    room = ROOMS[code]
    targets = [room.get("player_x"), room.get("player_o")]

    await asyncio.gather(*[
        send(ws, type, data) for ws in targets if ws
    ])


def find_room(ws):
    for code, room in ROOMS.items():
        if room.get("player_x") == ws or room.get("player_o") == ws:
            return code, room
    return None, None


def check_win(board):
    for a, b, c in WINNING_CONDITIONS:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a], [a, b, c]

    if "" not in board:
        return "TIE", None

    return None, None


# ------------------------------------------------------
# HANDLERS
# ------------------------------------------------------

async def handle_create(ws):
    code = generate_code()
    ROOMS[code] = {
        "player_x": ws,
        "player_o": None,
        "game_state": [""] * 9,
        "turn": "X"
    }
    await send(ws, "room_created", {"code": code})


async def handle_join(ws, data):
    code = data.get("code")

    if code not in ROOMS:
        return await send(ws, "error", {"message": "Room not found"})

    room = ROOMS[code]

    if room["player_o"]:
        return await send(ws, "error", {"message": "Room full"})

    room["player_o"] = ws

    await send(room["player_x"], "opponent_joined", {"code": code})
    await send(ws, "room_joined", {"code": code})


async def handle_move(ws, data):
    code = data.get("code")
    idx = data.get("index")
    player = data.get("player")

    if code not in ROOMS:
        return

    room = ROOMS[code]

    if room["turn"] != player:
        return

    if room["game_state"][idx] != "":
        return

    room["game_state"][idx] = player

    winner, win_line = check_win(room["game_state"])

    if winner:
        msg = "game_win" if winner != "TIE" else "game_tie"
        await broadcast(code, msg, {
            "winner": winner,
            "condition": win_line,
            "board": room["game_state"]
        })
        room["game_state"] = [""] * 9
        room["turn"] = "X"

    else:
        room["turn"] = "O" if player == "X" else "X"
        await broadcast(code, "game_move", {"index": idx, "player": player})
        await broadcast(code, "turn_switch", {"player": room["turn"]})


async def unregister(ws):
    code, room = find_room(ws)

    if not code:
        return

    symbol = "X" if room["player_x"] == ws else "O"

    if symbol == "X":
        room["player_x"] = None
    else:
        room["player_o"] = None

    if not room["player_x"] and not room["player_o"]:
        del ROOMS[code]
    else:
        await broadcast(code, "opponent_disconnected", {"symbol": symbol})


async def handler(ws, path):
    logging.info("Client connected")

    try:
        async for message in ws:
            data = json.loads(message)
            type = data.get("type")

            if type == "create_room":
                await handle_create(ws)
            elif type == "join_room":
                await handle_join(ws, data)
            elif type == "game_move":
                await handle_move(ws, data)

    except websockets.ConnectionClosed:
        pass

    finally:
        await unregister(ws)


# ------------------------------------------------------
# START SERVER (Railway)
# ------------------------------------------------------

async def main():
    port = int(os.environ.get("PORT", 8080))
    mimetypes.init()

    server = await websockets.serve(
        handler,
        "0.0.0.0",
        port,
        process_request=process_request
    )

    logging.info(f"Server started on ws://0.0.0.0:{port}")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
