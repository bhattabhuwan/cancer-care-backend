import eventlet
eventlet.monkey_patch()

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, emit, leave_room
from datetime import datetime, timezone, timedelta
import logging
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins="*", supports_credentials=True)

# CONFIG
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'change_this_secret_key'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=60)
app.config['SECRET_KEY'] = 'your-secret-key-here'

db = SQLAlchemy(app)
jwt = JWTManager(app)

socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    logger=True,
    engineio_logger=True,
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=10000000
)

# MODELS
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Call(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caller_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=False)
    call_uuid = db.Column(db.String(64), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    status = db.Column(db.String(32), default="initiated")
    started_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)

with app.app_context():
    db.create_all()

# HELPERS
def get_chat_room(user1, user2):
    return f"chat_room_{min(user1, user2)}_{max(user1, user2)}"

def get_call_room(call_uuid):
    return f"call_room_{call_uuid}"

connected_users = {}
active_calls = {}
call_room_users = {}  # Track users in each call room

def get_user_id_by_sid(sid):
    for uid, user_sid in connected_users.items():
        if user_sid == sid:
            return int(uid)
    return None

# SOCKET.IO EVENTS
@socketio.on("connect")
def handle_connect():
    try:
        user_id = request.args.get('userId')
        if user_id:
            connected_users[str(user_id)] = request.sid
            logger.info(f"✅ User {user_id} connected with SID {request.sid}")
            emit("connected", {"message": "Connected to chat server", "user_id": user_id}, room=request.sid)
        else:
            logger.warning("⚠️ User connected without user ID")
    except Exception as e:
        logger.error(f"❌ Error in connect: {e}")

@socketio.on("disconnect")
def handle_disconnect():
    try:
        user_id = get_user_id_by_sid(request.sid)
        if user_id:
            if str(user_id) in connected_users:
                del connected_users[str(user_id)]
            
            call_uuid_to_remove = None
            for call_uuid, call_data in active_calls.items():
                if call_data['caller_id'] == user_id or call_data['receiver_id'] == user_id:
                    call_uuid_to_remove = call_uuid
                    break
            
            if call_uuid_to_remove:
                del active_calls[call_uuid_to_remove]
                # Clean up call room users tracking
                if call_uuid_to_remove in call_room_users:
                    del call_room_users[call_uuid_to_remove]
            
            logger.info(f"❌ User {user_id} disconnected: {request.sid}")
            emit("user_disconnected", {"user_id": user_id}, broadcast=True)
            
    except Exception as e:
        logger.error(f"❌ Error in disconnect: {e}")

# JOIN ROOM & CHAT
@socketio.on("join")
def handle_join(data):
    try:
        sender_id = int(data['sender_id'])
        receiver_id = int(data['receiver_id'])
        sender_username = data.get('sender_username', 'Unknown')
        room = get_chat_room(sender_id, receiver_id)
        join_room(room)

        emit("system", {
            "message": f"{sender_username} joined the chat", 
            "timestamp": datetime.now(timezone.utc).isoformat()
        }, to=room)
        
        emit("joined_room", {
            "room": room, 
            "message": f"You joined chat with user {receiver_id}"
        }, room=request.sid)

        logger.info(f"✅ User {sender_id} joined room {room}")

    except Exception as e:
        logger.exception(f"❌ Error in join: {e}")
        emit("error", {"message": "Failed to join room"}, room=request.sid)

@socketio.on("send_message")
def handle_send_message(data):
    try:
        sender_id = int(data["sender_id"])
        receiver_id = int(data["receiver_id"])
        message_text = data["message"].strip()
        
        if not message_text:
            emit("error", {"message": "Message cannot be empty"}, room=request.sid)
            return

        msg = Message(
            sender_id=sender_id, 
            receiver_id=receiver_id, 
            message=message_text
        )
        db.session.add(msg)
        db.session.commit()
        db.session.refresh(msg)

        room = get_chat_room(sender_id, receiver_id)
        payload = {
            "sender_id": sender_id, 
            "receiver_id": receiver_id, 
            "message": message_text, 
            "timestamp": msg.timestamp.isoformat(), 
            "message_id": msg.id
        }

        emit("receive_message", payload, to=room)
        
        emit("message_sent", {
            "timestamp": msg.timestamp.isoformat(), 
            "message_id": msg.id
        }, room=request.sid)

        logger.info(f"✅ Message sent from {sender_id} to {receiver_id}")

    except Exception as e:
        logger.exception(f"❌ Error sending message: {e}")
        emit("error", {"message": "Failed to send message"}, room=request.sid)

# CALL REQUEST / RESPONSE - FIXED FOR PROPER CALL HANDLING
@socketio.on("call_request")
def handle_call_request(data):
    try:
        caller = int(data["from"])
        callee = int(data["to"])
        call_type = data.get("type", "video")

        logger.info(f"📞 Call request from {caller} to {callee} (type: {call_type})")

        call = Call(
            caller_id=caller, 
            receiver_id=callee, 
            status="ringing", 
            started_at=datetime.now(timezone.utc)
        )
        db.session.add(call)
        db.session.commit()
        db.session.refresh(call)

        logger.info(f"✅ Call record created: {call.call_uuid}")

        # Store call with type information
        active_calls[call.call_uuid] = {
            'caller_id': caller,
            'receiver_id': callee,
            'call_type': call_type,
            'status': 'ringing'
        }

        payload = {
            "call_uuid": call.call_uuid, 
            "from": caller, 
            "to": callee,
            "type": call_type, 
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        callee_sid = connected_users.get(str(callee))
        if callee_sid:
            emit("incoming_call", payload, room=callee_sid)
            logger.info(f"✅ Incoming call sent to callee {callee} with type: {call_type}")
        else:
            emit("call_failed", {
                "message": "User is offline", 
                "call_uuid": call.call_uuid
            }, room=request.sid)
            logger.warning(f"❌ Callee {callee} not connected")

    except Exception as e:
        logger.exception(f"❌ Error in call_request: {e}")
        emit("error", {"message": "Failed to request call"}, room=request.sid)

@socketio.on("call_response")
def handle_call_response(data):
    try:
        callee = int(data["from"])
        caller = int(data["to"])
        call_uuid = data.get("call_uuid")
        action = data.get("action")

        logger.info(f"📣 Call response: {action} from {callee} to {caller}, UUID: {call_uuid}")

        call = Call.query.filter_by(call_uuid=call_uuid).first()
        call_type = "video"
        
        if call:
            call.status = "accepted" if action == "accept" else "rejected"
            if action == "accept":
                call.started_at = datetime.now(timezone.utc)
                if call_uuid in active_calls:
                    active_calls[call_uuid]['status'] = 'accepted'
            db.session.commit()
            logger.info(f"✅ Call status updated to: {call.status}")
            
            # Get call_type from active_calls
            if call_uuid in active_calls:
                call_type = active_calls[call_uuid].get('call_type', 'video')

        caller_sid = connected_users.get(str(caller))
        if caller_sid:
            # Include type in response - FIXED FOR PROPER CALL TYPE HANDLING
            payload = {
                "call_uuid": call_uuid, 
                "from": callee, 
                "action": action,
                "type": call_type,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            emit("call_response", payload, room=caller_sid)
            logger.info(f"✅ Call response sent to caller {caller} with type: {call_type}")
            
            if action == "accept":
                call_room = get_call_room(call_uuid)
                
                caller_sid = connected_users.get(str(caller))
                callee_sid = connected_users.get(str(callee))
                
                if caller_sid:
                    join_room(call_room, sid=caller_sid)
                    logger.info(f"✅ Caller {caller} joined call room: {call_room}")
                
                if callee_sid:
                    join_room(call_room, sid=callee_sid)
                    logger.info(f"✅ Callee {callee} joined call room: {call_room}")
                
                # Track users in call room
                if call_uuid not in call_room_users:
                    call_room_users[call_uuid] = []
                
                if caller not in call_room_users[call_uuid]:
                    call_room_users[call_uuid].append(caller)
                if callee not in call_room_users[call_uuid]:
                    call_room_users[call_uuid].append(callee)
                
                logger.info(f"📊 Users in call room after accept: {call_room_users[call_uuid]}")
                # Don't send call_room_ready here - it will be sent by join_call_room handler when both users have joined
                    
                    
        else:
            logger.warning(f"❌ Caller {caller} not connected for response")

    except Exception as e:
        logger.exception(f"❌ Error in call_response: {e}")

# WEBRTC SIGNALING
@socketio.on("webrtc_offer")
def handle_webrtc_offer(data):
    try:
        target_id = int(data.get("to"))
        from_id = int(data.get("from"))
        
        logger.info(f"📨 WebRTC offer from {from_id} to {target_id}")
        
        target_sid = connected_users.get(str(target_id))
        if target_sid:
            emit("webrtc_offer", data, room=target_sid)
            logger.info(f"✅ WebRTC offer sent to {target_id}")
        else:
            logger.warning(f"❌ Target user {target_id} not connected")
            
    except Exception as e:
        logger.exception(f"❌ Error in webrtc_offer: {e}")

@socketio.on("webrtc_answer")
def handle_webrtc_answer(data):
    try:
        target_id = int(data.get("to"))
        from_id = int(data.get("from"))
        
        logger.info(f"📨 WebRTC answer from {from_id} to {target_id}")
        
        target_sid = connected_users.get(str(target_id))
        if target_sid:
            emit("webrtc_answer", data, room=target_sid)
            logger.info(f"✅ WebRTC answer sent to {target_id}")
        else:
            logger.warning(f"❌ Target user {target_id} not connected")
            
    except Exception as e:
        logger.exception(f"❌ Error in webrtc_answer: {e}")

@socketio.on("webrtc_ice_candidate")
def handle_webrtc_ice(data):
    try:
        target_id = int(data.get("to"))
        from_id = int(data.get("from"))
        
        logger.info(f"❄️ ICE candidate from {from_id} to {target_id}")
        
        target_sid = connected_users.get(str(target_id))
        if target_sid:
            emit("webrtc_ice_candidate", data, room=target_sid)
            logger.info(f"✅ ICE candidate sent to {target_id}")
        else:
            logger.warning(f"❌ Target user {target_id} not connected")
            
    except Exception as e:
        logger.exception(f"❌ Error in webrtc_ice_candidate: {e}")

@socketio.on("join_call_room")
def handle_join_call_room(data):
    try:
        call_uuid = data.get("call_uuid")
        user_id = data.get("user_id")
        
        call_room = get_call_room(call_uuid)
        join_room(call_room)
        
        # Track user in call room
        if call_uuid not in call_room_users:
            call_room_users[call_uuid] = []
        
        if user_id not in call_room_users[call_uuid]:
            call_room_users[call_uuid].append(user_id)
        
        logger.info(f"✅ User {user_id} joined call room: {call_room}")
        logger.info(f"   Users in call room: {call_room_users[call_uuid]}")
        
        # Check if both users are now in the call room
        if call_uuid in active_calls:
            caller_id = active_calls[call_uuid]['caller_id']
            receiver_id = active_calls[call_uuid]['receiver_id']
            call_type = active_calls[call_uuid].get('call_type', 'video')
            
            # Check if both users are present
            users_in_room = call_room_users.get(call_uuid, [])
            if caller_id in users_in_room and receiver_id in users_in_room:
                # Both users are now in the call room - send ready signal
                emit("call_room_ready", {
                    "call_uuid": call_uuid,
                    "call_room": call_room,
                    "participants": [caller_id, receiver_id],
                    "call_type": call_type
                }, to=call_room)
                logger.info(f"🎬 BOTH USERS IN CALL ROOM - Sending call_room_ready for {call_uuid}")
                logger.info(f"   Caller: {caller_id}, Receiver: {receiver_id}")
            else:
                # Still waiting for second user
                logger.info(f"⏳ Waiting for second user in call room {call_uuid}")
                logger.info(f"   Current users: {users_in_room}")
        
        emit("user_joined_call", {
            "user_id": user_id,
            "call_uuid": call_uuid
        }, to=call_room, skip_sid=request.sid)
        
    except Exception as e:
        logger.exception(f"❌ Error joining call room: {e}")

@socketio.on("leave_call_room")
def handle_leave_call_room(data):
    try:
        call_uuid = data.get("call_uuid")
        user_id = data.get("user_id")
        
        call_room = get_call_room(call_uuid)
        leave_room(call_room)
        
        logger.info(f"✅ User {user_id} left call room: {call_room}")
        
    except Exception as e:
        logger.exception(f"❌ Error leaving call room: {e}")

@socketio.on("end_call")
def handle_end_call(data):
    try:
        call_uuid = data.get("call_uuid")
        from_id = data.get("from")
        to_id = data.get("to")
        
        logger.info(f"⛔ Ending call: {call_uuid}")
        
        call = Call.query.filter_by(call_uuid=call_uuid).first()
        if call:
            call.status = "ended"
            call.ended_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f"✅ Call {call_uuid} marked as ended")

        if call_uuid in active_calls:
            del active_calls[call_uuid]
        
        # Clean up call room users tracking
        if call_uuid in call_room_users:
            del call_room_users[call_uuid]

        caller_sid = connected_users.get(str(from_id))
        callee_sid = connected_users.get(str(to_id))
        
        if caller_sid:
            emit("call_ended", {
                "from": from_id, 
                "call_uuid": call_uuid
            }, room=caller_sid)
            
        if callee_sid:
            emit("call_ended", {
                "from": from_id, 
                "call_uuid": call_uuid
            }, room=callee_sid)
        
        logger.info(f"✅ Call ended notifications sent")
            
    except Exception as e:
        logger.exception(f"❌ Error in end_call: {e}")

# TYPING INDICATORS
@socketio.on("typing")
def handle_typing(data):
    try:
        sender_id = int(data["sender_id"])
        receiver_id = int(data["receiver_id"])
        is_typing = data.get("typing", False)
        
        room = get_chat_room(sender_id, receiver_id)
        emit("typing", {
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "typing": is_typing
        }, to=room, skip_sid=request.sid)
        
    except Exception as e:
        logger.exception(f"❌ Error in typing: {e}")

# USER STATUS UPDATES
@socketio.on("update_user_status")
def handle_update_user_status(data):
    try:
        user_id = int(data["user_id"])
        status = data.get("status", "online")
        
        emit("user_status_update", {
            "user_id": user_id,
            "status": status
        }, broadcast=True)
        
        logger.info(f"👤 User {user_id} status updated to: {status}")
        
    except Exception as e:
        logger.exception(f"❌ Error updating user status: {e}")

# MESSAGE READ RECEIPTS
@socketio.on("mark_message_read")
def handle_mark_message_read(data):
    try:
        message_id = int(data["message_id"])
        receiver_id = int(data["receiver_id"])
        
        emit("message_read", {
            "message_id": message_id,
            "read_by": receiver_id
        }, broadcast=True)
        
    except Exception as e:
        logger.exception(f"❌ Error marking message as read: {e}")

# HTTP ROUTES
@app.route('/')
def index():
    return jsonify({
        "message": "Chat Server is running", 
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "connected_users": len(connected_users),
        "active_calls": len(active_calls)
    })

@app.route('/messages/<int:user1>/<int:user2>', methods=['GET'])
def get_message_history(user1, user2):
    try:
        messages = Message.query.filter(
            ((Message.sender_id == user1) & (Message.receiver_id == user2)) |
            ((Message.sender_id == user2) & (Message.receiver_id == user1))
        ).order_by(Message.timestamp.asc()).all()
        
        result = []
        for msg in messages:
            result.append({
                'id': msg.id,
                'sender_id': msg.sender_id,
                'receiver_id': msg.receiver_id,
                'message': msg.message,
                'timestamp': msg.timestamp.isoformat()
            })
        
        logger.info(f"✅ Fetched {len(result)} messages for users {user1} and {user2}")
        return jsonify(result)
        
    except Exception as e:
        logger.exception(f"❌ Error fetching message history: {e}")
        return jsonify({'error': 'Failed to fetch messages'}), 500

@app.route('/calls', methods=['GET'])
def get_calls():
    try:
        calls = Call.query.order_by(Call.started_at.desc()).limit(50).all()
        result = []
        for call in calls:
            result.append({
                'id': call.id,
                'caller_id': call.caller_id,
                'receiver_id': call.receiver_id,
                'call_uuid': call.call_uuid,
                'status': call.status,
                'started_at': call.started_at.isoformat() if call.started_at else None,
                'ended_at': call.ended_at.isoformat() if call.ended_at else None
            })
        return jsonify(result)
    except Exception as e:
        logger.exception(f"❌ Error fetching calls: {e}")
        return jsonify({'error': 'Failed to fetch calls'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy", 
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "server": "Flask-SocketIO",
        "version": "1.0"
    })

@app.route('/users/online', methods=['GET'])
def get_online_users():
    try:
        online_users = []
        for user_id, sid in connected_users.items():
            online_users.append({
                'user_id': int(user_id),
                'connected_at': 'active'
            })
        return jsonify({
            'online_users': online_users,
            'count': len(online_users)
        })
    except Exception as e:
        logger.exception(f"❌ Error fetching online users: {e}")
        return jsonify({'error': 'Failed to fetch online users'}), 500

# ERROR HANDLERS
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(400)
def bad_request(error):
    return jsonify({'error': 'Bad request'}), 400

# RUN SERVER
if __name__ == '__main__':
    print("🚀 Starting Chat Server on port 5001...")
    print("📡 Server will be available at: http://192.168.1.69:5001")
    print("🔧 Using eventlet for WebSocket support")
    print("✅ Call system properly configured with call_type handling")
    print("💡 Features:")
    print("   - Real-time messaging")
    print("   - Audio/Video calls with WebRTC")
    print("   - Typing indicators")
    print("   - User status updates")
    print("   - Message read receipts")
    
    try:
        socketio.run(
            app, 
            host='0.0.0.0', 
            port=5001, 
            debug=False,
            use_reloader=False,
            log_output=True
        )
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        print("💡 Troubleshooting tips:")
        print("   - Check if port 5001 is available")
        print("   - Ensure eventlet is installed: pip install eventlet")
        print("   - Check firewall settings")
        print("   - Verify network connectivity")
