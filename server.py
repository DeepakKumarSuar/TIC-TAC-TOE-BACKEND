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
    if '..' in filename or filename.startswith('/'): return HTTPStatus.NOT_FOUND, [], b'404 Not Found'
    try:
        with open(filename, 'rb') as f: content = f.read()
        mime_type, _ = mimetypes.guess_type(filename)
        headers = [('Content-Type', mime_type or 'application/octet-stream'), ('Content-Length', str(len(content)))]
        return HTTPStatus.OK, headers, content
    except FileNotFoundError: return HTTPStatus.NOT_FOUND, [], b'404 Not Found'
    except Exception: return HTTPStatus.INTERNAL_SERVER_ERROR, [], b'500 Internal Error'

async def process_request(path, request_headers):
    static_files = ['/', '/index.html', '/styles.css', '/main.py', '/TTT_Img.jpeg']
    if path in static_files:
        return await serve_static_file(path) 
    return None

def generate_unique_code():
    while True:
        code = str(random.randint(100000, 999999))
        if code not in ROOMS: return code

async def send_message(websocket, type, data=None):
    message = {'type': type, 'data': data if data is not None else {}}
    try: await websocket.send(json.dumps(message))
    except websockets.ConnectionClosed: pass

async def broadcast_message(room_code, type, data=None):
    if room_code in ROOMS:
        room = ROOMS[room_code]
        websockets_to_send = [room.get("player_x"), room.get("player_o")]
        await asyncio.wait([send_message(ws, type, data) for ws in websockets_to_send if ws])

def get_room_by_websocket(websocket):
    for code, room in ROOMS.items():
        if room.get("player_x") == websocket or room.get("player_o") == websocket: return code, room
    return None, None

def check_win(board):
    for condition in WINNING_CONDITIONS:
        a, b, c = condition
        if board[a] and board[a] == board[b] and board[a] == board[c]: return board[a], condition
    if "" not in board: return "TIE", None
    return None, None

async def handle_create_room(websocket):
    code = generate_unique_code()
    ROOMS[code] = {"player_x": websocket, "player_o": None, "game_state": [""] * 9, "current_player": "X"}
    await send_message(websocket, 'room_created', {'code': code})

async def handle_join_room(websocket, data):
    code = data.get('code')
    if code not in ROOMS: await send_message(websocket, 'error', {'message': "Room not found."}); return
    room = ROOMS[code]
    if room.get("player_o"): await send_message(websocket, 'error', {'message': "Room is full."}); return
    room["player_o"] = websocket
    if room["player_x"]: await send_message(room["player_x"], 'opponent_joined', {'code': code})
    await send_message(websocket, 'room_joined', {'code': code})

async def handle_game_move(websocket, data):
    code = data.get('code'); index = data.get('index'); player = data.get('player')
    if code not in ROOMS: return
    room = ROOMS[code]
    if room["current_player"] != player or room["game_state"][index] != "": return
        
    room["game_state"][index] = player
    winner, condition = check_win(room["game_state"])

    if winner:
        msg_type = 'game_win' if winner != 'TIE' else 'game_tie'
        msg_data = {'winner': winner, 'condition': condition, 'board': room["game_state"], 'index': index, 'player': player}
        await broadcast_message(code, msg_type, msg_data)
        room["game_state"] = [""] * 9
        room["current_player"] = "X" 
    else:
        room["current_player"] = "O" if player == "X" else "X"
        await broadcast_message(code, 'game_move', {'index': index, 'player': player})
        await broadcast_message(code, 'turn_switch', {'player': room["current_player"]})

async def unregister(websocket):
    code, room = get_room_by_websocket(websocket)
    if code and room:
        player_symbol = 'X' if room.get('player_x') == websocket else 'O'
        if player_symbol == 'X': room["player_x"] = None
        else: room["player_o"] = None

        if not room["player_x"] and not room["player_o"]: del ROOMS[code]
        else: await broadcast_message(code, 'opponent_disconnected', {'disconnected': player_symbol})

async def handler(websocket, path):
    try:
        async for message in websocket:
            try:
                data = json.loads(message); msg_type = data.get('type')
                if msg_type == 'create_room': await handle_create_room(websocket)
                elif msg_type == 'join_room': await handle_join_room(websocket, data)
                elif msg_type == 'game_move': await handle_game_move(websocket, data)
                elif msg_type == 'disconnect_room': await unregister(websocket)
            except json.JSONDecodeError: logging.error("Received invalid JSON.")
            except Exception as e: logging.error(f"Error handling message: {e}")
    except websockets.ConnectionClosed: pass
    finally: await unregister(websocket)

async def main():
    port = int(os.environ.get("PORT", 8080))
    mimetypes.init(); mimetypes.add_type('application/x-python', '.py'); mimetypes.add_type('image/jpeg', '.jpeg')
    
    asyncio.get_event_loop().run_until_complete(
    websockets.serve(handle_connection, "0.0.0.0", port)
)
asyncio.get_event_loop().run_forever()

if __name__ == "__main__":

    asyncio.run(main())
