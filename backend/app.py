from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
import cv2
import threading
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

# MongoDB Configuration
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['rtsp_livestream']
overlays_collection = db['overlays']
settings_collection = db['settings']

# RTSP Stream Configuration
current_stream = {'url': None, 'capture': None, 'active': False}
stream_lock = threading.Lock()


# Helper Functions
def serialize_doc(doc):
    """Convert MongoDB document to JSON-serializable format"""
    if doc:
        doc['_id'] = str(doc['_id'])
    return doc


def validate_overlay_data(data):
    """Validate overlay data"""
    required_fields = ['type', 'content', 'x', 'y', 'width', 'height']
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: {field}"
    
    if data['type'] not in ['text', 'logo']:
        return False, "Type must be 'text' or 'logo'"
    
    return True, None


# CRUD API Endpoints for Overlays

@app.route('/api/overlays', methods=['GET'])
def get_overlays():
    """Read all overlays"""
    try:
        overlays = list(overlays_collection.find())
        for overlay in overlays:
            overlay['_id'] = str(overlay['_id'])
        return jsonify({
            'success': True,
            'data': overlays,
            'count': len(overlays)
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/overlays/<overlay_id>', methods=['GET'])
def get_overlay(overlay_id):
    """Read single overlay by ID"""
    try:
        overlay = overlays_collection.find_one({'_id': ObjectId(overlay_id)})
        if not overlay:
            return jsonify({
                'success': False,
                'error': 'Overlay not found'
            }), 404
        
        return jsonify({
            'success': True,
            'data': serialize_doc(overlay)
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/overlays', methods=['POST'])
def create_overlay():
    """Create new overlay"""
    try:
        data = request.get_json()
        
        # Validate data
        is_valid, error = validate_overlay_data(data)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Add metadata
        data['created_at'] = datetime.utcnow()
        data['updated_at'] = datetime.utcnow()
        
        # Insert into database
        result = overlays_collection.insert_one(data)
        
        # Fetch created overlay
        overlay = overlays_collection.find_one({'_id': result.inserted_id})
        
        return jsonify({
            'success': True,
            'message': 'Overlay created successfully',
            'data': serialize_doc(overlay)
        }), 201
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/overlays/<overlay_id>', methods=['PUT'])
def update_overlay(overlay_id):
    """Update existing overlay"""
    try:
        data = request.get_json()
        
        # Validate data
        is_valid, error = validate_overlay_data(data)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Update metadata
        data['updated_at'] = datetime.utcnow()
        
        # Update in database
        result = overlays_collection.update_one(
            {'_id': ObjectId(overlay_id)},
            {'$set': data}
        )
        
        if result.matched_count == 0:
            return jsonify({
                'success': False,
                'error': 'Overlay not found'
            }), 404
        
        # Fetch updated overlay
        overlay = overlays_collection.find_one({'_id': ObjectId(overlay_id)})
        
        return jsonify({
            'success': True,
            'message': 'Overlay updated successfully',
            'data': serialize_doc(overlay)
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/overlays/<overlay_id>', methods=['DELETE'])
def delete_overlay(overlay_id):
    """Delete overlay"""
    try:
        result = overlays_collection.delete_one({'_id': ObjectId(overlay_id)})
        
        if result.deleted_count == 0:
            return jsonify({
                'success': False,
                'error': 'Overlay not found'
            }), 404
        
        return jsonify({
            'success': True,
            'message': 'Overlay deleted successfully'
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# CRUD API Endpoints for Settings

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get application settings"""
    try:
        settings = settings_collection.find_one({'type': 'app_settings'})
        if not settings:
            # Return default settings
            return jsonify({
                'success': True,
                'data': {
                    'rtsp_url': '',
                    'default_quality': 'high',
                    'auto_reconnect': True
                }
            }), 200
        
        return jsonify({
            'success': True,
            'data': serialize_doc(settings)
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update application settings"""
    try:
        data = request.get_json()
        data['updated_at'] = datetime.utcnow()
        
        # Upsert settings
        settings_collection.update_one(
            {'type': 'app_settings'},
            {'$set': data},
            upsert=True
        )
        
        return jsonify({
            'success': True,
            'message': 'Settings updated successfully',
            'data': data
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# RTSP Stream Management

@app.route('/api/stream/start', methods=['POST'])
def start_stream():
    """Start RTSP stream"""
    try:
        data = request.get_json()
        rtsp_url = data.get('rtsp_url')
        
        if not rtsp_url:
            return jsonify({
                'success': False,
                'error': 'RTSP URL is required'
            }), 400
        
        with stream_lock:
            # Stop existing stream if any
            if current_stream['capture']:
                current_stream['capture'].release()
            
            # Start new stream
            capture = cv2.VideoCapture(rtsp_url)
            if not capture.isOpened():
                return jsonify({
                    'success': False,
                    'error': 'Failed to open RTSP stream'
                }), 400
            
            current_stream['url'] = rtsp_url
            current_stream['capture'] = capture
            current_stream['active'] = True
        
        return jsonify({
            'success': True,
            'message': 'Stream started successfully',
            'stream_url': '/api/stream/video'
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stream/stop', methods=['POST'])
def stop_stream():
    """Stop RTSP stream"""
    try:
        with stream_lock:
            if current_stream['capture']:
                current_stream['capture'].release()
            current_stream['url'] = None
            current_stream['capture'] = None
            current_stream['active'] = False
        
        return jsonify({
            'success': True,
            'message': 'Stream stopped successfully'
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def generate_frames():
    """Generate video frames with overlays"""
    while current_stream['active']:
        with stream_lock:
            if not current_stream['capture']:
                break
            
            success, frame = current_stream['capture'].read()
            if not success:
                break
            
            # Apply overlays
            overlays = list(overlays_collection.find())
            for overlay in overlays:
                if overlay['type'] == 'text':
                    # Draw text overlay
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = overlay.get('fontSize', 24) / 30
                    color = tuple(int(overlay.get('color', '#ffffff')[i:i+2], 16) for i in (1, 3, 5))
                    cv2.putText(frame, overlay['content'], 
                              (overlay['x'], overlay['y']), 
                              font, font_scale, color, 2)
            
            # Encode frame
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')


@app.route('/api/stream/video')
def video_feed():
    """Video streaming route"""
    return Response(generate_frames(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'success': True,
        'message': 'API is running',
        'timestamp': datetime.utcnow().isoformat()
    }), 200


if __name__ == '__main__':
    port= int(os.getenv('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)