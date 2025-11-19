# server.py
import asyncio
import websockets
import json
import logging
import random
import os
from http import HTTPStatus
import mimetypes
from pathlib import Path

# ---------- Configuration ----------
logging.basicConfig(level=logging.INFO)
BASE_DIR = Path(__file__).parent.resolve()

ROOMS = {}
WINNING_CONDITIONS = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8],
    [0, 3, 6], [1, 4, 7], [2, 5, 8],
    [0, 4, 8], [2, 4, 6]
]

# ---------- Static file serving helpers ----------
async def serve_static_file(path: str):
    """
    Serve a static file from the repository root.
    Returns (HTTPStatus, headers_list, body_bytes) or raises.
    """
    # map '/' -> index.html
    filename = 'index.html' if path == '/' else path.lstrip('/')
    # prevent path traversal
    if '..' in filename or filename.startswith('/'):
        return HTTPStatus.NOT_FOUND, [], b'404 Not Found'

    file_path = BASE_DIR / filename
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        mime_type, _ = mimetypes.guess_type(str(file_path))
        headers = [
            ('Content-Type', mime_type or 'application/octet-stream'),
            ('Content-Length', str(len(content)))
        ]
        return HTTPStatus.OK, headers, content
    except FileNotFoundError:
        return HTTPStatus.NOT_FOUND, [], b'404 Not Found'
    except Exception:
        return HTTPStatus.INTERNAL_SERVER_ERROR, [], b'500 Internal Error'

async def process_request(path: str, request_headers):
    """
    Called for incoming HTTP requests (before WebSocket handshake).
    If returns None, websockets will continue with WebSocket handshake.
    If returns (status, headers, body), websockets will return that HTTP response.
    """
    # allow serving these static files
    static_files = ['/', '/index.html', '/styles.css', '/main.py', '/TTT_Img.jpeg']
    if path in static_files:
        return await serve_static_file(path)
    # not a static file → proceed to WebSocket handshake
    return None

# ---------- Game / Room logic ----------
def generate_unique_code():
    while True:
        code = str(random.randint(100000, 999999))
        if code not in ROOMS:
            return code

async def send_message(websocket, type_, data=None):
    """
    Send a JSON message with structure: {type: ..., data: {...}}
    """
    message = {'type': type_, 'data': data if data is not None else {}}
    try:
        await websocket.send(json.dumps(message))
    except websockets.ConnectionClosed:
        pass

async def broadcast_message(room_code, type_, data=None):
    """
    Send a message to both players in a room (if present).
    """
    if room_code not in ROOMS:
        return
    room = ROOMS[room_code]
    recipients = [room.get("player_x"), room.get("player_o")]
    tasks = [send_message(ws, type_, data) for ws in recipients if ws]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

def get_room_by_websocket(websocket):
    for code, room in ROOMS.items():
        if room.get("player_x") == websocket or room.get("player_o") == websocket:
            return code, room
    return None, None

def check_win(board):
    for condition in WINNING_CONDITIONS:
        a, b, c = condition
        if board[a] and board[a] == board[b] and board[a] == board[c]:
            return board[a], condition
    if "" not in board:
        return "TIE", None
    return None, None

# ---------- Handlers for game messages ----------
async def handle_create_room(websocket):
    code = generate_unique_code()
    ROOMS[code] = {
        "player_x": websocket,
        "player_o": None,
        "game_state": [""] * 9,
        "current_player": "X"
    }
    logging.info(f"Room created: {code}")
    await send_message(websocket, 'room_created', {'code': code})

async def handle_join_room(websocket, data):
    code = data.get('code')
    if not code or code not in ROOMS:
        await send_message(websocket, 'error', {'message': "Room not found."})
        return
    room = ROOMS[code]
    if room.get("player_o"):
        await send_message(websocket, 'error', {'message': "Room is full."})
        return
    room["player_o"] = websocket
    # notify both
    if room.get("player_x"):
        await send_message(room["player_x"], 'opponent_joined', {'code': code})
    await send_message(websocket, 'room_joined', {'code': code})
    logging.info(f"Player joined room {code}")

async def handle_game_move(websocket, data):
    code = data.get('code')
    index = data.get('index')
    player = data.get('player')
    if code not in ROOMS:
        return
    room = ROOMS[code]
    # simple validation
    if index is None or player is None:
        return
    try:
        index = int(index)
    except Exception:
        return
    if index < 0 or index > 8:
        return

    if room["current_player"] != player or room["game_state"][index] != "":
        # invalid move
        return

    room["game_state"][index] = player
    winner, condition = check_win(room["game_state"])

    if winner:
        msg_type = 'game_win' if winner != 'TIE' else 'game_tie'
        msg_data = {
            'winner': winner,
            'condition': condition,
            'board': room["game_state"],
            'index': index,
            'player': player
        }
        await broadcast_message(code, msg_type, msg_data)
        # reset for next game (optional)
        room["game_state"] = [""] * 9
        room["current_player"] = "X"
    else:
        room["current_player"] = "O" if player == "X" else "X"
        await broadcast_message(code, 'game_move', {'index': index, 'player': player})
        await broadcast_message(code, 'turn_switch', {'player': room["current_player"]})

async def unregister(websocket):
    code, room = get_room_by_websocket(websocket)
    if not code or not room:
        return
    player_symbol = 'X' if room.get('player_x') == websocket else 'O'
    if player_symbol == 'X':
        room["player_x"] = None
    else:
        room["player_o"] = None

    if not room["player_x"] and not room["player_o"]:
        # delete room entirely
        del ROOMS[code]
        logging.info(f"Room {code} deleted (empty).")
    else:
        # notify the remaining player
        await broadcast_message(code, 'opponent_disconnected', {'disconnected': player_symbol})
        logging.info(f"Player {player_symbol} disconnected from room {code}.")

# ---------- Primary WebSocket handler ----------
async def handler(websocket, path):
    """
    Handles incoming WebSocket messages. The `process_request` function
    already responds to static file HTTP requests and only attempts
    WebSocket handshake for other paths.
    """
    logging.info(f"New WebSocket connection. Path: {path}")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logging.warning("Received non-JSON message; ignoring.")
                continue

            msg_type = data.get('type')
            if msg_type == 'create_room':
                await handle_create_room(websocket)
            elif msg_type == 'join_room':
                await handle_join_room(websocket, data)
            elif msg_type == 'game_move':
                await handle_game_move(websocket, data)
            elif msg_type == 'disconnect_room':
                await unregister(websocket)
            else:
                logging.debug(f"Unknown message type: {msg_type} | raw: {data}")
    except websockets.ConnectionClosed:
        # client disconnected
        pass
    finally:
        await unregister(websocket)

# ---------- Main: start server ----------
async def main():
    port = int(os.environ.get("PORT", 8080))
    mimetypes.init()
    mimetypes.add_type('application/x-python', '.py')
    mimetypes.add_type('image/jpeg', '.jpeg')

    server = await websockets.serve(
        handler,
        "0.0.0.0",
        port,
        process_request=process_request
    )

    logging.info(f"WebSocket server running on port {port}")
    await server.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.exception("Server crashed on startup: %s", e)
