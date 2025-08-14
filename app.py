from flask import Flask, render_template, jsonify, request, flash, redirect, url_for, Response
import os
import sqlite3
import json
import hashlib
from datetime import datetime, timezone
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
import logging
import pytz
from tzlocal import get_localzone
import folium

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['DEBUG'] = True
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'raw_files')
app.config['DATABASE_PATH'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'workouts.db')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.dirname(app.config['DATABASE_PATH']), exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def detect_user_timezone():
    try:
        return get_localzone()
    except Exception as e:
        logger.warning(f"Could not detect local timezone: {e}, defaulting to UTC")
        return pytz.UTC

def convert_utc_to_local(utc_datetime, target_timezone=None):
    if target_timezone is None:
        target_timezone = detect_user_timezone()
    
    if utc_datetime.tzinfo is None:
        utc_datetime = pytz.UTC.localize(utc_datetime)
    elif utc_datetime.tzinfo != pytz.UTC:
        utc_datetime = utc_datetime.astimezone(pytz.UTC)
    
    return utc_datetime.astimezone(target_timezone)

def convert_local_to_utc(local_datetime, source_timezone=None):
    if source_timezone is None:
        source_timezone = detect_user_timezone()
    
    if local_datetime.tzinfo is None:
        local_datetime = source_timezone.localize(local_datetime)
    
    return local_datetime.astimezone(pytz.UTC)

def parse_timestamp_with_timezone(timestamp_str):
    try:
        if timestamp_str.endswith('Z'):
            timestamp_str = timestamp_str.replace('Z', '+00:00')
        
        dt = datetime.fromisoformat(timestamp_str)
        
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        
        return dt
    except ValueError as e:
        logger.warning(f"Could not parse timestamp {timestamp_str}: {e}")
        return None

def format_datetime_for_display(dt, target_timezone=None):
    if dt is None:
        return None
    
    if target_timezone is None:
        target_timezone = detect_user_timezone()
    
    local_dt = convert_utc_to_local(dt, target_timezone)

    return local_dt.isoformat()

USER_TIMEZONE = detect_user_timezone()
logger.info(f"Detected user timezone: {USER_TIMEZONE}")

def init_database():
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            parsed_data_path TEXT,
            upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            workout_type TEXT,
            start_time DATETIME,
            end_time DATETIME,
            duration_seconds INTEGER,
            distance_meters REAL,
            total_calories INTEGER,
            avg_heart_rate INTEGER,
            max_heart_rate INTEGER,
            avg_power_watts INTEGER,
            max_power_watts INTEGER,
            avg_cadence INTEGER,
            max_cadence INTEGER,
            avg_speed_mps REAL,
            max_speed_mps REAL,
            elevation_gain_meters REAL,
            elevation_loss_meters REAL,
            notes TEXT,
            processed BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_workouts_start_time ON workouts(start_time)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_workouts_workout_type ON workouts(workout_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_workouts_file_hash ON workouts(file_hash)')
    
    conn.commit()
    conn.close()

def get_db_connection():
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    return conn

def calculate_file_hash(file_data):
    return hashlib.sha256(file_data).hexdigest()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'fit'

def parse_fit_file(file_path):
    try:
        from fitparse import FitFile
        
        fitfile = FitFile(file_path)
        
        parsed_data = {
            "file_info": {
                "file_path": file_path,
                "file_size": os.path.getsize(file_path),
                "parsed_at": format_datetime_for_display(datetime.now(pytz.UTC))
            },
            "workout_summary": {},
            "metrics": {},
            "gps_data": [],
            "sensor_data": []
        }
        
        for record in fitfile.get_messages('session'):
            for record_data in record:
                if record_data.name == 'sport':
                    parsed_data['workout_summary']['type'] = record_data.value
                elif record_data.name == 'start_time':
                    if record_data.value:
                        start_time_utc = pytz.UTC.localize(record_data.value) if record_data.value.tzinfo is None else record_data.value
                        parsed_data['workout_summary']['start_time'] = format_datetime_for_display(start_time_utc)
                    else:
                        parsed_data['workout_summary']['start_time'] = None
                elif record_data.name == 'total_elapsed_time':
                    parsed_data['workout_summary']['duration'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'total_distance':
                    parsed_data['workout_summary']['distance'] = float(record_data.value) if record_data.value else None
                elif record_data.name == 'total_calories':
                    parsed_data['workout_summary']['calories'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'avg_heart_rate':
                    if 'heart_rate' not in parsed_data['metrics']:
                        parsed_data['metrics']['heart_rate'] = {}
                    parsed_data['metrics']['heart_rate']['avg'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'max_heart_rate':
                    if 'heart_rate' not in parsed_data['metrics']:
                        parsed_data['metrics']['heart_rate'] = {}
                    parsed_data['metrics']['heart_rate']['max'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'avg_power':
                    if 'power' not in parsed_data['metrics']:
                        parsed_data['metrics']['power'] = {}
                    parsed_data['metrics']['power']['avg'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'max_power':
                    if 'power' not in parsed_data['metrics']:
                        parsed_data['metrics']['power'] = {}
                    parsed_data['metrics']['power']['max'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'avg_cadence':
                    if 'cadence' not in parsed_data['metrics']:
                        parsed_data['metrics']['cadence'] = {}
                    parsed_data['metrics']['cadence']['avg'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'max_cadence':
                    if 'cadence' not in parsed_data['metrics']:
                        parsed_data['metrics']['cadence'] = {}
                    parsed_data['metrics']['cadence']['max'] = int(record_data.value) if record_data.value else None
                elif record_data.name == 'avg_speed':
                    if 'speed' not in parsed_data['metrics']:
                        parsed_data['metrics']['speed'] = {}
                    parsed_data['metrics']['speed']['avg'] = float(record_data.value) if record_data.value else None
                elif record_data.name == 'max_speed':
                    if 'speed' not in parsed_data['metrics']:
                        parsed_data['metrics']['speed'] = {}
                    parsed_data['metrics']['speed']['max'] = float(record_data.value) if record_data.value else None
                elif record_data.name == 'total_ascent':
                    if 'elevation' not in parsed_data['metrics']:
                        parsed_data['metrics']['elevation'] = {}
                    parsed_data['metrics']['elevation']['gain'] = float(record_data.value) if record_data.value else None
                elif record_data.name == 'total_descent':
                    if 'elevation' not in parsed_data['metrics']:
                        parsed_data['metrics']['elevation'] = {}
                    parsed_data['metrics']['elevation']['loss'] = float(record_data.value) if record_data.value else None
        
        sensor_records = []
        for record in fitfile.get_messages('record'):
            record_data_dict = {}
            for record_data in record:
                if record_data.name in ['timestamp', 'position_lat', 'position_long', 'altitude', 
                                       'heart_rate', 'power', 'cadence', 'speed', 'distance']:
                    if record_data.value is not None:
                        if record_data.name == 'timestamp':
                            timestamp_utc = pytz.UTC.localize(record_data.value) if record_data.value.tzinfo is None else record_data.value
                            record_data_dict[record_data.name] = format_datetime_for_display(timestamp_utc)
                        else:
                            record_data_dict[record_data.name] = record_data.value
            
            if record_data_dict:
                sensor_records.append(record_data_dict)
                
                if 'position_lat' in record_data_dict and 'position_long' in record_data_dict:
                    gps_point = {
                        'timestamp': record_data_dict.get('timestamp'),
                        'lat': record_data_dict['position_lat'],
                        'lon': record_data_dict['position_long'],
                        'altitude': record_data_dict.get('altitude')
                    }
                    parsed_data['gps_data'].append(gps_point)

        processed_sensor_data = []
        calculated_speeds = []
        all_speeds = []
        
        for i, record in enumerate(sensor_records):
            processed_record = record.copy()
            
            if 'distance' in record and 'timestamp' in record and i > 0:
                prev_record = sensor_records[i - 1]
                if 'distance' in prev_record and 'timestamp' in prev_record:
                    try:
                        curr_time = parse_timestamp_with_timezone(record['timestamp'])
                        prev_time = parse_timestamp_with_timezone(prev_record['timestamp'])
                        
                        if curr_time and prev_time:
                            time_diff = (curr_time - prev_time).total_seconds()
                            
                            if time_diff > 0:
                                distance_diff = record['distance'] - prev_record['distance']
                                if distance_diff >= 0:  # Only positive distance changes
                                    calculated_speed = distance_diff / time_diff  # m/s
                                    
                                    max_speed_ms = 22.0  # ~80 km/h
                                    min_speed_ms = 0.14  # ~0.5 km/h
                                    
                                    if calculated_speed <= max_speed_ms and calculated_speed >= min_speed_ms:
                                        processed_record['calculated_speed'] = calculated_speed
                                        calculated_speeds.append(calculated_speed)
                                        
                                        if 'speed' not in processed_record or processed_record['speed'] is None:
                                            processed_record['speed'] = calculated_speed
                                        
                                        all_speeds.append(processed_record['speed'])
                                    else:
                                        logger.debug(f"Skipping unrealistic calculated speed: {calculated_speed:.2f} m/s ({calculated_speed * 3.6:.1f} km/h)")
                                        if 'speed' in processed_record and processed_record['speed'] is not None:
                                            all_speeds.append(processed_record['speed'])
                    except (ValueError, KeyError, TypeError) as e:
                        logger.debug(f"Error calculating speed for record {i}: {e}")
            elif 'speed' in processed_record and processed_record['speed'] is not None:
                existing_speed = processed_record['speed']
                max_speed_ms = 22.0 
                min_speed_ms = 0.0
                
                if existing_speed <= max_speed_ms and existing_speed >= min_speed_ms:
                    all_speeds.append(existing_speed)
                else:
                    logger.debug(f"Skipping unrealistic FIT speed data: {existing_speed:.2f} m/s ({existing_speed * 3.6:.1f} km/h)")
                    processed_record['speed'] = None
            
            processed_sensor_data.append(processed_record)
        
        parsed_data['sensor_data'] = processed_sensor_data
        
        if all_speeds:
            speed_metrics = {
                'avg': sum(all_speeds) / len(all_speeds),
                'max': max(all_speeds),
                'min': min(all_speeds)
            }
            
            if calculated_speeds:
                parsed_data['metrics']['calculated_speed'] = {
                    'avg': sum(calculated_speeds) / len(calculated_speeds),
                    'max': max(calculated_speeds),
                    'min': min(calculated_speeds)
                }
            
            if 'speed' not in parsed_data['metrics']:
                parsed_data['metrics']['speed'] = {}
            
            if not parsed_data['metrics']['speed'].get('avg'):
                parsed_data['metrics']['speed']['avg'] = speed_metrics['avg']
            if not parsed_data['metrics']['speed'].get('max'):
                parsed_data['metrics']['speed']['max'] = speed_metrics['max']
        
        has_actual_power = any(record.get('power') is not None for record in processed_sensor_data)
        
        chart_data = {
            'heart_rate': [],
            'power': [],
            'cadence': [],
            'speed': [],
            'timestamps': [],
            'distance': []
        }
        
        for record in processed_sensor_data:
            if 'timestamp' in record:
                chart_data['timestamps'].append(record['timestamp'])
                chart_data['heart_rate'].append(record.get('heart_rate'))
                power_value = record.get('power')
                chart_data['power'].append(power_value)
                chart_data['cadence'].append(record.get('cadence'))
                chart_data['speed'].append(record.get('speed', record.get('calculated_speed')))
                chart_data['distance'].append(record.get('distance'))
        
        parsed_data['data_quality'] = {
            'has_actual_power': has_actual_power,
            'has_calculated_speed': len(calculated_speeds) > 0
        }
        
        parsed_data['chart_data'] = chart_data
        
        logger.info(f"Successfully parsed FIT file: {file_path}")
        return parsed_data
        
    except ImportError:
        logger.warning("fitparse library not available, using mock data")
        return {
            "file_info": {
                "file_path": file_path,
                "file_size": os.path.getsize(file_path),
                "parsed_at": format_datetime_for_display(datetime.now(pytz.UTC))
            },
            "workout_summary": {
                "type": "unknown",
                "start_time": format_datetime_for_display(datetime.now(pytz.UTC)),
                "duration": 3600,
                "distance": 10000,
                "calories": 300
            },
            "metrics": {
                "heart_rate": {"avg": 140, "max": 170},
                "power": {"avg": 180, "max": 350},
                "cadence": {"avg": 80, "max": 110},
                "speed": {"avg": 6.0, "max": 12.0}
            },
            "gps_data": [],
            "sensor_data": []
        }
        
    except Exception as e:
        logger.error(f"Error parsing FIT file {file_path}: {str(e)}")
        return None

def store_workout_metadata(file_hash, filename, file_path, parsed_data):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        workout_summary = parsed_data.get('workout_summary', {})
        metrics = parsed_data.get('metrics', {})
        
        parsed_data_filename = f"{file_hash}.json"
        parsed_data_path = os.path.join(
            os.path.dirname(app.config['UPLOAD_FOLDER']), 
            'parsed_data', 
            parsed_data_filename
        )
        
        os.makedirs(os.path.dirname(parsed_data_path), exist_ok=True)
        
        with open(parsed_data_path, 'w') as f:
            json.dump(parsed_data, f, indent=2)
        
        cursor.execute('''
            INSERT OR REPLACE INTO workouts (
                file_hash, filename, file_path, parsed_data_path,
                workout_type, start_time, duration_seconds, distance_meters,
                total_calories, avg_heart_rate, max_heart_rate,
                avg_power_watts, max_power_watts, avg_cadence, max_cadence,
                avg_speed_mps, max_speed_mps, elevation_gain_meters, elevation_loss_meters, processed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            file_hash, filename, file_path, parsed_data_path,
            workout_summary.get('type'),
            workout_summary.get('start_time'),
            workout_summary.get('duration'),
            workout_summary.get('distance'),
            workout_summary.get('calories'),
            metrics.get('heart_rate', {}).get('avg'),
            metrics.get('heart_rate', {}).get('max'),
            metrics.get('power', {}).get('avg'),
            metrics.get('power', {}).get('max'),
            metrics.get('cadence', {}).get('avg'),
            metrics.get('cadence', {}).get('max'),
            metrics.get('calculated_speed', {}).get('avg') or metrics.get('speed', {}).get('avg'),
            metrics.get('calculated_speed', {}).get('max') or metrics.get('speed', {}).get('max'),
            metrics.get('elevation', {}).get('gain'),
            metrics.get('elevation', {}).get('loss'),
            True
        ))
        
        conn.commit()
        logger.info(f"Stored metadata for workout: {filename}")
        return cursor.lastrowid
        
    except Exception as e:
        logger.error(f"Error storing workout metadata: {str(e)}")
        conn.rollback()
        return None
    finally:
        conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/workouts', methods=['GET'])
def get_workouts():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        workout_type = request.args.get('type')
        
        where_clause = ""
        params = []
        
        if workout_type:
            where_clause = "WHERE workout_type = ?"
            params.append(workout_type)
        
        query = f'''
            SELECT * FROM workouts 
            {where_clause}
            ORDER BY start_time DESC 
            LIMIT ? OFFSET ?
        '''
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        workouts = [dict(row) for row in cursor.fetchall()]
        
        count_query = f"SELECT COUNT(*) FROM workouts {where_clause}"
        cursor.execute(count_query, params[:-2] if workout_type else [])
        total_count = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'workouts': workouts,
            'total_count': total_count,
            'limit': limit,
            'offset': offset
        })
        
    except Exception as e:
        logger.error(f"Error fetching workouts: {str(e)}")
        return jsonify({'error': 'Failed to fetch workouts'}), 500

@app.route('/api/workouts/<int:workout_id>', methods=['GET'])
def get_workout_detail(workout_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM workouts WHERE id = ?', (workout_id,))
        workout = cursor.fetchone()
        
        if not workout:
            return jsonify({'error': 'Workout not found'}), 404
        
        workout_dict = dict(workout)
        
        if workout['parsed_data_path'] and os.path.exists(workout['parsed_data_path']):
            with open(workout['parsed_data_path'], 'r') as f:
                workout_dict['parsed_data'] = json.load(f)
        
        conn.close()
        return jsonify(workout_dict)
        
    except Exception as e:
        logger.error(f"Error fetching workout detail: {str(e)}")
        return jsonify({'error': 'Failed to fetch workout detail'}), 500

@app.route('/api/workouts/<int:workout_id>/raw', methods=['GET'])
def get_workout_raw_data(workout_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM workouts WHERE id = ?', (workout_id,))
        workout = cursor.fetchone()
        
        if not workout:
            return jsonify({'error': 'Workout not found'}), 404
        
        if workout['parsed_data_path'] and os.path.exists(workout['parsed_data_path']):
            with open(workout['parsed_data_path'], 'r') as f:
                parsed_data = json.load(f)
        else:
            return jsonify({'error': 'Parsed data not available'}), 404
        
        conn.close()
        
        return jsonify({
            'workout_id': workout_id,
            'filename': workout['filename'],
            'file_hash': workout['file_hash'],
            'upload_timestamp': workout['upload_timestamp'],
            'parsed_data': parsed_data,
            'metadata': {
                'total_sensor_records': len(parsed_data.get('sensor_data', [])),
                'total_gps_points': len(parsed_data.get('gps_data', [])),
                'file_size_bytes': parsed_data.get('file_info', {}).get('file_size', 0),
                'parsed_at': parsed_data.get('file_info', {}).get('parsed_at', 'Unknown')
            }
        })
        
    except Exception as e:
        logger.error(f"Error fetching raw workout data: {str(e)}")
        return jsonify({'error': 'Failed to fetch raw workout data'}), 500

@app.route('/api/workouts/<int:workout_id>/chart', methods=['GET'])
def get_workout_chart_data(workout_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM workouts WHERE id = ?', (workout_id,))
        workout = cursor.fetchone()
        
        if not workout:
            return jsonify({'error': 'Workout not found'}), 404
        
        workout = dict(zip([description[0] for description in cursor.description], workout))
        
        if not workout['parsed_data_path'] or not os.path.exists(workout['parsed_data_path']):
            return jsonify({'error': 'Chart data not available'}), 404
        
        with open(workout['parsed_data_path'], 'r') as f:
            parsed_data = json.load(f)
        
        conn.close()
        
        chart_data = parsed_data.get('chart_data', {})
        data_quality = parsed_data.get('data_quality', {})
        
        if not chart_data or not any(chart_data.values()):
            return jsonify({'error': 'No chart data available'}), 404
        
        return jsonify({
            'workout_id': workout_id,
            'chart_data': chart_data,
            'has_heart_rate': bool(chart_data.get('heart_rate') and any(hr for hr in chart_data['heart_rate'] if hr is not None)),
            'has_speed': bool(chart_data.get('speed') and any(s for s in chart_data['speed'] if s is not None)),
            'has_power': bool(chart_data.get('power') and any(p for p in chart_data['power'] if p is not None)),
            'has_cadence': bool(chart_data.get('cadence') and any(c for c in chart_data['cadence'] if c is not None)),
            'data_quality': data_quality
        })
        
    except Exception as e:
        logger.error(f"Error fetching chart data: {str(e)}")
        return jsonify({'error': 'Failed to fetch chart data'}), 500

@app.route('/api/workouts/<int:workout_id>/map', methods=['GET'])
def get_workout_map_data(workout_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM workouts WHERE id = ?', (workout_id,))
        workout = cursor.fetchone()
        
        if not workout:
            return jsonify({'error': 'Workout not found'}), 404
        
        workout = dict(zip([description[0] for description in cursor.description], workout))
        
        if not workout['parsed_data_path'] or not os.path.exists(workout['parsed_data_path']):
            return jsonify({'error': 'GPS data not available'}), 404
        
        with open(workout['parsed_data_path'], 'r') as f:
            parsed_data = json.load(f)
        
        conn.close()
        
        gps_data = parsed_data.get('gps_data', [])
        
        processed_gps_data = []
        for point in gps_data:
            if 'lat' in point and 'lon' in point and point['lat'] is not None and point['lon'] is not None:
                lat = float(point['lat'])
                lon = float(point['lon'])
                
                if abs(lat) > 180:
                    lat = lat * (180 / (2**31))
                if abs(lon) > 180:
                    lon = lon * (180 / (2**31))
                
                if -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
                    processed_point = {
                        'lat': lat,
                        'lon': lon,
                        'timestamp': point.get('timestamp'),
                        'altitude': point.get('altitude')
                    }
                    processed_gps_data.append(processed_point)
        
        if not processed_gps_data:
            return jsonify({'error': 'No valid GPS data available'}), 404
        
        total_points = len(processed_gps_data)
        start_point = processed_gps_data[0]
        end_point = processed_gps_data[-1]
        
        lats = [p['lat'] for p in processed_gps_data]
        lons = [p['lon'] for p in processed_gps_data]
        bounds = {
            'north': max(lats),
            'south': min(lats),
            'east': max(lons),
            'west': min(lons)
        }
        
        return jsonify({
            'workout_id': workout_id,
            'gps_data': processed_gps_data,
            'stats': {
                'total_points': total_points,
                'start_point': start_point,
                'end_point': end_point,
                'bounds': bounds
            }
        })
        
    except Exception as e:
        logger.error(f"Error fetching GPS data: {str(e)}")
        return jsonify({'error': 'Failed to fetch GPS data'}), 500

@app.route('/api/workouts/<int:workout_id>', methods=['DELETE'])
def delete_workout(workout_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT file_path, parsed_data_path FROM workouts WHERE id = ?', (workout_id,))
        workout = cursor.fetchone()
        
        if not workout:
            return jsonify({'error': 'Workout not found'}), 404
        
        file_path, parsed_data_path = workout
        
        cursor.execute('DELETE FROM workouts WHERE id = ?', (workout_id,))
        conn.commit()
        conn.close()
        
        files_deleted = []
        files_failed = []
        
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                files_deleted.append(file_path)
            except OSError as e:
                files_failed.append(f"Raw file: {str(e)}")
        
        if parsed_data_path and os.path.exists(parsed_data_path):
            try:
                os.remove(parsed_data_path)
                files_deleted.append(parsed_data_path)
            except OSError as e:
                files_failed.append(f"Parsed data: {str(e)}")
        
        logger.info(f"Deleted workout {workout_id}. Files deleted: {len(files_deleted)}, Failed: {len(files_failed)}")
        
        return jsonify({
            'message': 'Workout deleted successfully',
            'workout_id': workout_id,
            'files_deleted': len(files_deleted),
            'files_failed': files_failed if files_failed else None
        }), 200
        
    except Exception as e:
        logger.error(f"Error deleting workout {workout_id}: {str(e)}")
        return jsonify({'error': 'Failed to delete workout'}), 500

@app.route('/api/upload', methods=['POST'])
def upload_fit_file():
    try:
        if 'fit_file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['fit_file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Only .fit files are allowed'}), 400
        
        file_data = file.read()
        file_hash = calculate_file_hash(file_data)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM workouts WHERE file_hash = ?', (file_hash,))
        existing = cursor.fetchone()
        
        if existing:
            conn.close()
            return jsonify({'error': 'File already exists', 'workout_id': existing[0]}), 409
        
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_filename = f"{timestamp}_{file_hash[:8]}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        
        with open(file_path, 'wb') as f:
            f.write(file_data)
        
        parsed_data = parse_fit_file(file_path)
        
        if parsed_data is None:
            os.remove(file_path)
            return jsonify({'error': 'Failed to parse FIT file'}), 400
        
        workout_id = store_workout_metadata(file_hash, filename, file_path, parsed_data)
        
        if workout_id is None:
            os.remove(file_path)
            return jsonify({'error': 'Failed to store workout data'}), 500
        
        conn.close()
        logger.info(f"Successfully uploaded and processed FIT file: {filename}")
        
        return jsonify({
            'message': 'File uploaded and processed successfully',
            'workout_id': workout_id,
            'filename': filename,
            'file_hash': file_hash
        }), 201
        
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        return jsonify({'error': 'Upload failed'}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM workouts')
        total_workouts = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM workouts WHERE processed = TRUE')
        processed_workouts = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT workout_type, COUNT(*) as count 
            FROM workouts 
            WHERE workout_type IS NOT NULL 
            GROUP BY workout_type
        ''')
        workout_types = [{'type': row[0], 'count': row[1]} for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT DATE(upload_timestamp) as date, COUNT(*) as count
            FROM workouts
            GROUP BY DATE(upload_timestamp)
            ORDER BY date DESC
        ''')
        recent_activity = [{'date': row[0], 'count': row[1]} for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            'total_workouts': total_workouts,
            'processed_workouts': processed_workouts,
            'workout_types': workout_types,
            'recent_activity': recent_activity
        })
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        return jsonify({'error': 'Failed to fetch statistics'}), 500

@app.route('/api/timezone', methods=['GET'])
def get_timezone_info():
    try:
        user_tz = detect_user_timezone()
        current_time_utc = datetime.now(pytz.UTC)
        current_time_local = convert_utc_to_local(current_time_utc, user_tz)
        
        return jsonify({
            'timezone': str(user_tz),
            'timezone_name': user_tz.zone if hasattr(user_tz, 'zone') else str(user_tz),
            'current_time_utc': current_time_utc.isoformat(),
            'current_time_local': current_time_local.isoformat(),
            'utc_offset': current_time_local.strftime('%z'),
            'utc_offset_hours': current_time_local.utcoffset().total_seconds() / 3600
        })
    except Exception as e:
        logger.error(f"Error getting timezone info: {str(e)}")
        return jsonify({'error': 'Failed to get timezone information'}), 500

def _extract_valid_gps_points(parsed_data):
    gps_data = parsed_data.get('gps_data', []) if parsed_data else []
    processed = []
    for p in gps_data:
        lat = p.get('lat')
        lon = p.get('lon')
        if lat is None or lon is None:
            continue
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue
        if abs(lat) > 180:
            lat = lat * (180 / (2**31))
        if abs(lon) > 180:
            lon = lon * (180 / (2**31))
        if -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
            processed.append({'lat': lat, 'lon': lon, 'altitude': p.get('altitude'), 'timestamp': p.get('timestamp')})
    return processed

def _build_folium_map(points, theme: str = 'light'):
    if not points:
        is_dark = (theme == 'dark')
        bg = '#111827' if is_dark else '#f8fafc'
        fg = '#e5e7eb' if is_dark else '#374151'
        css = (
            "body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,sans-serif;" 
            f"background:{bg};color:{fg}}}" 
            " .box{display:flex;align-items:center;justify-content:center;height:100vh;}"
        )
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<style>{css}</style></head><body><div class='box'><div>No GPS data available</div></div></body></html>"
        )
        return html

    lats = [p['lat'] for p in points]
    lons = [p['lon'] for p in points]
    center = (sum(lats) / len(lats), sum(lons) / len(lons))

    theme = 'dark' if theme == 'dark' else 'light'
    tiles = 'CartoDB dark_matter' if theme == 'dark' else 'CartoDB positron'
    line_color = '#60a5fa' if theme == 'dark' else '#3b82f6'

    m = folium.Map(location=center, tiles=tiles, zoom_start=14, control_scale=True)

    coords = [(p['lat'], p['lon']) for p in points]
    folium.PolyLine(coords, color=line_color, weight=4, opacity=0.9).add_to(m)

    start = coords[0]
    end = coords[-1]
    folium.Marker(start, tooltip='Start', icon=folium.Icon(color='green', icon='play', prefix='fa')).add_to(m)
    folium.Marker(end, tooltip='Finish', icon=folium.Icon(color='red', icon='flag-checkered', prefix='fa')).add_to(m)

    m.fit_bounds([ (min(lats), min(lons)), (max(lats), max(lons)) ], padding=(20, 20))

    return m.get_root().render()

@app.route('/api/workouts/<int:workout_id>/map/folium', methods=['GET'])
def get_workout_folium_map(workout_id: int):
    try:
        theme = request.args.get('theme', 'light').lower()
        if theme not in ('light', 'dark'):
            theme = 'light'
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT parsed_data_path FROM workouts WHERE id = ?', (workout_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return Response('<h3>Workout not found</h3>', status=404, mimetype='text/html')
        parsed_path = row[0]
        conn.close()

        if not parsed_path or not os.path.exists(parsed_path):
            return Response('<h3>No GPS data available</h3>', status=404, mimetype='text/html')

        with open(parsed_path, 'r') as f:
            parsed_data = json.load(f)

        points = _extract_valid_gps_points(parsed_data)
        html = _build_folium_map(points, theme)
        return Response(html, mimetype='text/html')
    except Exception as e:
        logger.exception('Error generating Folium map')
        return Response('<h3>Error generating map</h3>', status=500, mimetype='text/html')

if __name__ == '__main__':
    init_database()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(
        host='0.0.0.0',
        port=port,
        debug=app.config['DEBUG']
    )
