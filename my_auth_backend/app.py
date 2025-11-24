from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import (
    JWTManager, create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity
)
from flask_cors import CORS
from datetime import timedelta
import logging

# Swagger
from flasgger import Swagger

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
swagger = Swagger(app)
CORS(app)  # Enable CORS

# Configs
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///user.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'change_this_to_a_strong_secret_key_in_production'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=60)
app.config['JWT_ERROR_MESSAGE_KEY'] = 'msg'

db = SQLAlchemy(app)
jwt = JWTManager(app)

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

# JWT Callbacks
@jwt.invalid_token_loader
def invalid_token_callback(error):
    logger.warning("Invalid token attempted")
    return jsonify({'msg': 'Invalid token'}), 422

@jwt.unauthorized_loader
def missing_token_callback(error):
    logger.warning("Unauthorized access attempt")
    return jsonify({'msg': 'Authorization required'}), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    logger.warning("Expired token used")
    return jsonify({'msg': 'Token has expired', 'error': 'token_expired'}), 401

# Root route
@app.route('/')
def home():
    """
    Home route
    ---
    responses:
      200:
        description: API is running
    """
    return "API is running!", 200

# Register
@app.route('/register', methods=['POST'])
def register():
    """
    Register a new user
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: body
        required: true
        schema:
          id: Register
          required:
            - username
            - email
            - password
          properties:
            username:
              type: string
              example: johndoe
            email:
              type: string
              example: johndoe@example.com
            password:
              type: string
              example: secret123
    responses:
      201:
        description: User created successfully
      400:
        description: Bad request
    """
    try:
        data = request.get_json() or {}
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        if not (username and email and password):
            return jsonify({'msg': 'Username, email and password required'}), 400

        if User.query.filter((User.email == email) | (User.username == username)).first():
            return jsonify({'msg': 'User already exists'}), 400

        hashed = generate_password_hash(password)
        user = User(username=username, email=email, password=hashed)
        db.session.add(user)
        db.session.commit()

        access_token = create_access_token(identity=str(user.id), additional_claims={'username': user.username})
        refresh_token = create_refresh_token(identity=str(user.id), additional_claims={'username': user.username})

        return jsonify({
            'msg': 'User created',
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user': {'id': user.id, 'username': user.username, 'email': user.email}
        }), 201
    except Exception:
        logger.exception("Error during registration")
        return jsonify({'msg': 'Server error during registration'}), 500

# Login
@app.route('/login', methods=['POST'])
def login():
    """
    User Login
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: body
        required: true
        schema:
          id: Login
          required:
            - email
            - password
          properties:
            email:
              type: string
              example: johndoe@example.com
            password:
              type: string
              example: secret123
    responses:
      200:
        description: Login successful
      401:
        description: Invalid credentials
    """
    try:
        data = request.get_json() or {}
        email = data.get('email')
        password = data.get('password')
        if not (email and password):
            return jsonify({'msg': 'Email and password required'}), 400

        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            return jsonify({'msg': 'Invalid credentials'}), 401

        access_token = create_access_token(identity=str(user.id), additional_claims={'username': user.username})
        refresh_token = create_refresh_token(identity=str(user.id), additional_claims={'username': user.username})

        return jsonify({
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user': {'id': user.id, 'username': user.username, 'email': user.email}
        }), 200
    except Exception:
        logger.exception("Error during login")
        return jsonify({'msg': 'Server error during login'}), 500

# Forgot password
@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    """
    Forgot password
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: body
        required: true
        schema:
          id: ForgotPassword
          required:
            - email
          properties:
            email:
              type: string
              example: johndoe@example.com
    responses:
      200:
        description: Reset token generated
      400:
        description: Email not found
    """
    try:
        data = request.get_json()
        email = data.get('email')
        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({'msg': 'Email not found'}), 400

        reset_token = create_access_token(
            identity=str(user.id),
            expires_delta=timedelta(minutes=15),
            additional_claims={'reset': True}
        )
        return jsonify({'reset_token': reset_token}), 200
    except Exception:
        logger.exception("Error during forgot-password")
        return jsonify({'msg': 'Server error'}), 500

# Reset password
@app.route('/reset-password', methods=['POST'])
def reset_password():
    """
    Reset password using reset token
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: body
        required: true
        schema:
          id: ResetPassword
          required:
            - token
            - new_password
          properties:
            token:
              type: string
            new_password:
              type: string
    responses:
      200:
        description: Password updated
      400:
        description: Invalid token
    """
    try:
        data = request.get_json()
        token = data.get('token')
        new_password = data.get('new_password')
        decoded = jwt.decode_token(token)
        user_id = decoded['sub']
        user = User.query.get(user_id)
        if not user:
            return jsonify({'msg': 'Invalid token'}), 400

        user.password = generate_password_hash(new_password)
        db.session.commit()
        return jsonify({'msg': 'Password updated'}), 200
    except Exception:
        logger.exception("Error during reset-password")
        return jsonify({'msg': 'Server error'}), 500

# Refresh token
@app.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    """
    Refresh access token
    ---
    tags:
      - Auth
    parameters:
      - in: header
        name: Authorization
        required: true
    responses:
      200:
        description: New access token
    """
    current_user = get_jwt_identity()
    access_token = create_access_token(identity=current_user)
    return jsonify({'access_token': access_token}), 200

# Protected route
@app.route('/protected', methods=['GET'])
@jwt_required()
def protected():
    """
    Protected route
    ---
    tags:
      - Auth
    parameters:
      - in: header
        name: Authorization
        required: true
    responses:
      200:
        description: Success message
    """
    current_user = get_jwt_identity()
    return jsonify({'msg': f"Hello {current_user}"}), 200

# List users
@app.route('/users', methods=['GET'])
@jwt_required()
def list_users():
    """
    List all users
    ---
    tags:
      - Users
    parameters:
      - in: header
        name: Authorization
        required: true
    responses:
      200:
        description: Returns list of users
    """
    users = User.query.all()
    result = [{'id': u.id, 'username': u.username, 'email': u.email} for u in users]
    return jsonify(result), 200

# Delete user
@app.route('/delete/<int:user_id>', methods=['DELETE'])
@jwt_required()
def delete_user(user_id):
    """
    Delete user by ID
    ---
    tags:
      - Users
    parameters:
      - in: path
        name: user_id
        required: true
    responses:
      200:
        description: User deleted
      404:
        description: User not found
    """
    user = User.query.get(user_id)
    if not user:
        return jsonify({'msg': 'User not found'}), 404
    db.session.delete(user)
    db.session.commit()
    return jsonify({'msg': 'User deleted'}), 200

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # create tables
    app.run(host='0.0.0.0', port=5000, debug=True)
