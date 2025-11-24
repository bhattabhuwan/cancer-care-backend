# Chat App - Flask Backend

A simple real-time chat backend with Flask and Socket.IO.

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install flask flask-socketio flask-sqlalchemy flask-jwt-extended flask-cors
```

### 2. Run the Server
```bash
python app.py
```
Server starts on: `http://localhost:5001`

## 📁 What It Does

- **Real-time chat** between users
- **Message history** stored in SQLite
- **Private rooms** for each user pair
- **Auto timezone handling** (UTC storage)

## 🔌 API Endpoints

### Chat History
```
GET /messages/1/2
```
Returns messages between user 1 and user 2

### Health Check
```
GET /health
```
Server status check

## 💬 WebSocket Events

### Join Chat Room
```javascript
socket.emit('join', {
  sender_id: 1,
  receiver_id: 2, 
  sender_username: "john"
})
```

### Send Message
```javascript
socket.emit('send_message', {
  sender_id: 1,
  receiver_id: 2,
  message: "Hello!"
})
```

### Receive Messages
```javascript
socket.on('receive_message', (data) => {
  console.log(data.message)
})
```

## ⚙️ Configuration

- **Port**: 5001
- **Database**: SQLite (auto-created)
- **CORS**: Enabled for all origins

## 🛠️ Files Created

- `chat.db` - SQLite database (auto-created)
- Message table with: id, sender_id, receiver_id, message, timestamp

## 🔒 Security Notes

- Change `JWT_SECRET_KEY` in production
- Use HTTPS in production
- Add user authentication as needed

---

**Ready to chat!** 🎉 Just run `python app.py` and connect your Flutter app.
