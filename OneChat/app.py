from flask import Flask, render_template, url_for, request, session, make_response, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_session import Session
import uuid
import random
import requests
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import hashlib
import json
from datetime import datetime
import logging
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)
socketio = SocketIO(app, manage_session=False)

limiter = Limiter(key_func=get_remote_address, app=app)

# In-memory storage for rooms, chat history, and users
rooms = ['default']
chat_history_file = 'chat_history.json'
chat_history = {}
users = {}

def generate_nickname():
    adj_response = requests.get('https://random-word-form.herokuapp.com/random/adjective')
    adjective = adj_response.json()[0] if adj_response.status_code == 200 else 'Anonymous'
    noun_response = requests.get('https://random-word-form.herokuapp.com/random/noun')
    noun = noun_response.json()[0] if noun_response.status_code == 200 else 'User'
    numbers = ''.join([str(random.randint(0, 9)) for _ in range(random.randint(2, 3))])
    return f"{adjective.capitalize()}{noun.capitalize()}{numbers}"

def get_browser_fingerprint():
    user_agent = request.headers.get('User-Agent')
    accept_language = request.headers.get('Accept-Language')
    fingerprint = f"{user_agent}{accept_language}{request.remote_addr}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()

@app.route('/')
@limiter.limit("5 per day")
def index():
    fingerprint = get_browser_fingerprint()
    user_id = request.cookies.get('user_id')
    nickname = request.cookies.get('nickname')

    if not user_id or not nickname:
        user_id = str(uuid.uuid4())
        nickname = generate_nickname()

    session['user_id'] = user_id
    session['nickname'] = nickname

    # Load rooms and chat history
    load_or_create_chat_history()

    response = make_response(render_template('index.html', user_id=user_id, nickname=nickname, rooms=rooms))
    response.set_cookie('user_id', user_id, max_age=31536000, httponly=True, secure=True)  # 1 year
    response.set_cookie('nickname', nickname, max_age=31536000, httponly=True, secure=True)  # 1 year

    return response

@app.route('/dev')
def dev_account():
    user_id = str(uuid.uuid4())
    nickname = f"Dev{generate_nickname()}"
    session['user_id'] = user_id
    session['nickname'] = nickname
    session['is_dev'] = True
    return render_template('index.html', user_id=user_id, nickname=nickname, rooms=rooms)

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    if room not in rooms:
        rooms.append(room)
        chat_history[room] = []
    users[request.sid] = {
        'nickname': session['nickname'],
        'room': room,
        'online': True
    }
    emit('message', {'message': f'{session["nickname"]} has joined the room.', 'nickname': 'System', 'room': room}, room=room)
    emit('update_rooms', {'rooms': rooms}, broadcast=True)
    emit('user_list', {'users': get_users_in_room(room)}, room=room)

@socketio.on('leave')
def on_leave(data):
    room = data['room']
    leave_room(room)
    users[request.sid]['room'] = None
    emit('message', {'message': f'{session["nickname"]} has left the room.', 'nickname': 'System', 'room': room}, room=room)
    emit('user_list', {'users': get_users_in_room(room)}, room=room)

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in users:
        room = users[request.sid]['room']
        users[request.sid]['online'] = False
        emit('user_list', {'users': get_users_in_room(room)}, room=room)

@socketio.on('get_user_list')
def get_user_list(data):
    room = data['room']
    emit('user_list', {'users': get_users_in_room(room)})

def get_users_in_room(room):
    return [
        {
            'nickname': user['nickname'],
            'online': user['online']
        }
        for user in users.values()
        if user['room'] == room
    ]

@socketio.on('message')
def handle_message(data):
    room = data['room']
    message_data = {
        'message': data['message'],
        'userId': data['userId'],
        'nickname': session.get('nickname', 'Anonymous'),
        'room': room,
        'timestamp': datetime.now().isoformat()
    }
    chat_history[room].append(message_data)
    emit('message', message_data, room=room, include_self=False)  # Don't send to the sender
    save_chat_history_to_file()  # Save chat history after each new message

@socketio.on('typing')
def handle_typing(data):
    room = data['room']
    emit('typing', {
        'isTyping': data['isTyping'],
        'room': room,
        'nickname': session.get('nickname', 'Anonymous'),
        'userId': data['userId']
    }, room=room)

@socketio.on('disconnect')
def handle_disconnect():
    if session.get('is_dev'):
        # Clear dev session data
        session.pop('user_id', None)
        session.pop('nickname', None)
        session.pop('is_dev', None)

@socketio.on('get_chat_history')
def get_chat_history(data):
    room = data['room']
    emit('chat_history', {'history': chat_history.get(room, [])})

@socketio.on('connect')
def handle_connect():
    emit('update_rooms', {'rooms': rooms})

def load_or_create_chat_history():
    global chat_history, rooms
    try:
        with open(chat_history_file, 'r') as f:
            chat_history = json.load(f)
        rooms = list(chat_history.keys())
    except FileNotFoundError:
        chat_history = {'default': []}
        rooms = ['default']
        save_chat_history_to_file()
    except json.JSONDecodeError:
        print("Error decoding chat history file. Creating a new one.")
        chat_history = {'default': []}
        rooms = ['default']
        save_chat_history_to_file()

def save_chat_history_to_file():
    with open(chat_history_file, 'w') as f:
        json.dump(chat_history, f)

# Load or create chat history on startup
load_or_create_chat_history()

if __name__ == '__main__':
    socketio.run(app, debug=True, port=8080)