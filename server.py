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

async def serve_static_file(path):
    filename = 'index.html' if path == '/' else path.lstrip('/')
    if '..' in filename or filename.startswith('/'):
        return HTTPStatus.NOT_FOUND, [], b'404 Not Found'
    try:
        with open(filename, 'rb') as f:
            content = f.read()
        mime_type, _ = mimetypes.guess_type(filename)
        headers = [('Content-Type', mime_type or 'application/octet-stream'),
                   ('Content-Length', str(len(content)))]
        return HTTPStatus.OK, headers, content
    except FileNotFoundError:
        return HTTPStatus.NOT_FOUND, [], b'404 Not Found'
    except Exception:
        return HTTPStatus.INTERNAL_SERVER_ERROR, [], b'500 Internal Error'

async def process_request(path, request_headers):
    static_files = ['/', '/index.html', '/styles.css', '/main.py', '/TTT_Img.jpeg']
    if path in static_files:
        return await serve_static_file(path)
    return None

def generate_unique_code():
    while True:
        code = str(random.randint(0, 101))
        if code not in ROOMS:
            return code

async def send_message(websocket, type, data=None):
    message = {'type': type, 'data': data if data else {}}
    try:
        await websocket.send(json.dumps(message))
    except websockets.ConnectionClosed:
        pass

async def broadcast_message(room_code, type, data=None):
    if room_code in ROOMS:
        room = ROOMS[room_code]
        targets = [room.get("player_x"), room.get("player_o")]
        await asyncio.wait([send_message(ws, type, data) for ws in targets if ws])

def get_room_by_websocket(ws):
    for code, room in ROOMS.items():
        if room.get("player_x") == ws or room.get("player_o") == ws:
            return code, room
    return None, None

def check_win(board):
    for a,b,c in WINNING_CONDITIONS:
        if board[a] and board[a] == board[b] and board[a] == board[c]:
            return board[a], [a,b,c]
    if "" not in board:
        return "TIE", None
    return None, None

async def handle_create_room(ws):
    code = generate_unique_code()
    ROOMS[code] = {
        "player_x": ws,
        "player_o": None,
        "game_state": [""] * 9,
        "current_player": "X"
    }
    await send_message(ws, 'room_created', {'code': code})

async def handle_join_room(ws, data):
    code = data.get('code')
    if code not in ROOMS:
        await send_message(ws, 'error', {'message': "Room not found."})
        return

    room = ROOMS[code]
    if room["player_o"]:
        await send_message(ws, 'error', {'message': "Room is full."})
        return

    room["player_o"] = ws

    await send_message(ws, 'room_joined', {'code': code})
    await send_message(room["player_x"], 'opponent_joined', {'code': code})

async def handle_game_move(ws, data):
    code = data.get('code')
    index = data.get('index')
    player = data.get('player')

    if code not in ROOMS:
        return

    room = ROOMS[code]

    if room["current_player"] != player:
        return

    if room["game_state"][index] != "":
        return

    room["game_state"][index] = player

    winner, condition = check_win(room["game_state"])

    if winner:
        await broadcast_message(code, 'game_win' if winner != "TIE" else 'game_tie', {
            'winner': winner,
            'condition': condition,
            'board': room['game_state'],
            'index': index,
            'player': player
        })

        room["game_state"] = [""] * 9
        room["current_player"] = "X"
    else:
        room["current_player"] = "O" if player == "X" else "X"
        await broadcast_message(code, 'game_move', {'index': index, 'player': player})
        await broadcast_message(code, 'turn_switch', {'player': room["current_player"]})

async def unregister(ws):
    code, room = get_room_by_websocket(ws)
    if not code:
        return

    player = 'X' if room.get("player_x") == ws else 'O'

    if player == 'X':
        room["player_x"] = None
    else:
        room["player_o"] = None

    if not room["player_x"] and not room["player_o"]:
        del ROOMS[code]
    else:
        await broadcast_message(code, 'opponent_disconnected', {'disconnected': player})

async def handler(ws, path):
    try:
        async for msg in ws:
            data = json.loads(msg)
            t = data.get('type')

            if t == 'create_room':
                await handle_create_room(ws)
            elif t == 'join_room':
                await handle_join_room(ws, data)
            elif t == 'game_move':
                await handle_game_move(ws, data)
            elif t == 'disconnect_room':
                await unregister(ws)

    except websockets.ConnectionClosed:
        pass
    finally:
        await unregister(ws)

async def main():
    port = int(os.environ.get("PORT", 8080))

    server = await websockets.serve(
        handler,
        "0.0.0.0",
        port,
        process_request=process_request
    )

    logging.info(f"WebSocket server started on {port}")
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())

