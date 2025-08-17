from flask import Flask, render_template, jsonify, request, flash, redirect, url_for, Response
import os
import sqlite3
import json
import hashlib
from datetime import datetime, timezone, timedelta
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
            name TEXT,
            tags TEXT,
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
    
    try:
        cursor.execute("PRAGMA table_info(workouts)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'name' not in cols:
            cursor.execute('ALTER TABLE workouts ADD COLUMN name TEXT')
        if 'tags' not in cols:
            cursor.execute('ALTER TABLE workouts ADD COLUMN tags TEXT')
    except Exception as e:
        logger.warning(f"Could not ensure name/tags columns exist: {e}")

    conn.commit()
    conn.close()

def _ensure_workout_name_column(conn: sqlite3.Connection):
    try:
        cur = conn.cursor()
        cur.execute('PRAGMA table_info(workouts)')
        cols = [r[1] for r in cur.fetchall()]
        if 'name' not in cols:
            cur.execute('ALTER TABLE workouts ADD COLUMN name TEXT')
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to ensure 'name' column: {e}")

def _ensure_workout_tags_column(conn: sqlite3.Connection):
    try:
        cur = conn.cursor()
        cur.execute('PRAGMA table_info(workouts)')
        cols = [r[1] for r in cur.fetchall()]
        if 'tags' not in cols:
            cur.execute('ALTER TABLE workouts ADD COLUMN tags TEXT')
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to ensure 'tags' column: {e}")

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


@app.route("/devices")
def list_devices():
    devices = []
    
    try:
        import glob
        import os
        
        gvfs_mount_dirs = glob.glob("/run/user/*/gvfs/mtp:host=*")
        for mtp_dir in gvfs_mount_dirs:
            try:
                parts = mtp_dir.split("mtp:host=")
                if len(parts) > 1:
                    device_id = parts[1].split("/")[0] if "/" in parts[1] else parts[1]
                    
                    device_name = ""
                    try:
                        if os.path.exists(mtp_dir):
                            contents = os.listdir(mtp_dir)
                            device_name = device_id
                    except Exception:
                        device_name = device_id
                    
                    if "garmin" in device_name.lower() or "garmin" in device_id.lower():
                        devices.append({
                            "device": f"mtp://{device_id}",
                            "mountpoint": mtp_dir,
                            "fstype": "mtp",
                            "type": "mtp",
                            "removable": True,
                            "name": device_name
                        })
                        logger.info(f"Found Garmin MTP device: {device_name} at {mtp_dir}")
                    else:
                        logger.debug(f"Skipping non-Garmin MTP device: {device_name}")
                        
            except Exception as e:
                logger.debug(f"Error processing MTP device {mtp_dir}: {e}")
                
    except ImportError:
        logger.debug("glob not available for MTP detection")
    except Exception as e:
        logger.warning(f"Error detecting MTP devices: {e}")
    
    return jsonify(devices)

def find_fit_files_on_device(mount_path):
    """Find .fit files specifically in the Activities folder on a mounted MTP device"""
    fit_files = []
    try:
        import os
        
        # Recursively search for Activities folder
        activities_path = None
        for root, dirs, files in os.walk(mount_path):
            if 'Activities' in dirs:
                activities_path = os.path.join(root, 'Activities')
                break
        
        if not activities_path or not os.path.exists(activities_path):
            logger.debug(f"Activities folder not found in {mount_path}")
            return fit_files
            
        logger.info(f"Found Activities folder: {activities_path}")
        
        # Scan only the Activities folder for .fit files
        try:
            for file in os.listdir(activities_path):
                if file.lower().endswith('.fit'):
                    full_path = os.path.join(activities_path, file)
                    try:
                        stat = os.stat(full_path)
                        fit_files.append({
                            'path': full_path,
                            'filename': file,
                            'size': stat.st_size,
                            'modified': stat.st_mtime
                        })
                        logger.debug(f"Found .fit file: {file}")
                    except OSError as e:
                        logger.debug(f"Could not stat file {full_path}: {e}")
                        continue
        except OSError as e:
            logger.warning(f"Could not list files in Activities folder {activities_path}: {e}")
            
    except Exception as e:
        logger.error(f"Error scanning for .fit files in Activities folder of {mount_path}: {e}")
    
    logger.info(f"Found {len(fit_files)} .fit files in Activities folder")
    return fit_files

@app.route('/api/devices/scan-fit-files', methods=['POST'])
def scan_and_upload_fit_files():
    """Scan all detected Garmin devices for .fit files and upload them"""
    try:
        import glob
        import os
        import shutil
        
        results = {
            'devices_scanned': 0,
            'files_found': 0,
            'files_uploaded': 0,
            'files_skipped': 0,
            'errors': []
        }
        
        # Get all Garmin MTP devices
        gvfs_mount_dirs = glob.glob("/run/user/*/gvfs/mtp:host=*")
        garmin_devices = []
        
        for mtp_dir in gvfs_mount_dirs:
            try:
                parts = mtp_dir.split("mtp:host=")
                if len(parts) > 1:
                    device_id = parts[1].split("/")[0] if "/" in parts[1] else parts[1]
                    
                    # Check if it's a Garmin device
                    if "garmin" in device_id.lower():
                        if os.path.exists(mtp_dir):
                            garmin_devices.append({
                                'device_id': device_id,
                                'mount_path': mtp_dir
                            })
                            
            except Exception as e:
                results['errors'].append(f"Error processing device {mtp_dir}: {str(e)}")
        
        results['devices_scanned'] = len(garmin_devices)
        
        if not garmin_devices:
            return jsonify({
                **results,
                'message': 'No Garmin MTP devices found. Make sure your Garmin device is connected via USB and mounted as MTP.'
            })
        
        # Scan each device for .fit files
        for device in garmin_devices:
            logger.info(f"Scanning Garmin device {device['device_id']} for .fit files...")
            
            try:
                fit_files = find_fit_files_on_device(device['mount_path'])
                results['files_found'] += len(fit_files)
                
                for fit_file in fit_files:
                    try:
                        # Read the file data
                        with open(fit_file['path'], 'rb') as f:
                            file_data = f.read()
                        
                        # Calculate hash to check if file already exists
                        file_hash = calculate_file_hash(file_data)
                        
                        # Check if file already exists in database
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute('SELECT id FROM workouts WHERE file_hash = ?', (file_hash,))
                        existing = cursor.fetchone()
                        conn.close()
                        
                        if existing:
                            results['files_skipped'] += 1
                            logger.debug(f"File {fit_file['filename']} already exists, skipping")
                            continue
                        
                        # Save file to upload folder
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        safe_filename = f"{timestamp}_{file_hash[:8]}_{secure_filename(fit_file['filename'])}"
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
                        
                        with open(file_path, 'wb') as f:
                            f.write(file_data)
                        
                        # Parse and store the workout
                        parsed_data = parse_fit_file(file_path)
                        
                        if parsed_data is None:
                            os.remove(file_path)
                            results['errors'].append(f"Failed to parse {fit_file['filename']}")
                            continue
                        
                        workout_id = store_workout_metadata(file_hash, fit_file['filename'], file_path, parsed_data)
                        
                        if workout_id is None:
                            os.remove(file_path)
                            results['errors'].append(f"Failed to store {fit_file['filename']}")
                            continue
                        
                        results['files_uploaded'] += 1
                        logger.info(f"Successfully uploaded {fit_file['filename']} from Garmin device")
                        
                    except Exception as e:
                        results['errors'].append(f"Error processing {fit_file['filename']}: {str(e)}")
                        logger.error(f"Error processing fit file {fit_file['filename']}: {e}")
                        
            except Exception as e:
                results['errors'].append(f"Error scanning device {device['device_id']}: {str(e)}")
                logger.error(f"Error scanning device {device['device_id']}: {e}")
        
        message = f"Scan complete. Found {results['files_found']} files, uploaded {results['files_uploaded']}, skipped {results['files_skipped']}"
        if results['errors']:
            message += f", {len(results['errors'])} errors"
        
        return jsonify({
            **results,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Error in scan_and_upload_fit_files: {str(e)}")
        return jsonify({'error': 'Failed to scan devices', 'details': str(e)}), 500

@app.route('/api/devices/fit-files', methods=['GET'])
def list_fit_files_on_devices():
    """List all .fit files found on connected Garmin devices without uploading"""
    try:
        import glob
        import os
        
        results = {
            'devices': [],
            'total_files': 0
        }
        
        # Get all Garmin MTP devices
        gvfs_mount_dirs = glob.glob("/run/user/*/gvfs/mtp:host=*")
        
        for mtp_dir in gvfs_mount_dirs:
            try:
                parts = mtp_dir.split("mtp:host=")
                if len(parts) > 1:
                    device_id = parts[1].split("/")[0] if "/" in parts[1] else parts[1]
                    
                    # Check if it's a Garmin device
                    if "garmin" in device_id.lower():
                        if os.path.exists(mtp_dir):
                            fit_files = find_fit_files_on_device(mtp_dir)
                            
                            device_info = {
                                'device_id': device_id,
                                'mount_path': mtp_dir,
                                'fit_files': fit_files,
                                'file_count': len(fit_files)
                            }
                            
                            results['devices'].append(device_info)
                            results['total_files'] += len(fit_files)
                            
            except Exception as e:
                logger.error(f"Error listing files on device {mtp_dir}: {e}")
        
        return jsonify(results)
        
    except Exception as e:
        logger.error(f"Error listing fit files: {str(e)}")
        return jsonify({'error': 'Failed to list fit files', 'details': str(e)}), 500

@app.route('/api/workouts', methods=['GET'])
def get_workouts():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        workout_type = request.args.get('type')
        tag_filter = request.args.get('tag')  # filter by tag name (case-insensitive contains)
        
        where_clauses = []
        params = []
        
        if workout_type:
            where_clauses.append("workout_type = ?")
            params.append(workout_type)
        if tag_filter:
            # Tags stored as JSON array in TEXT; use LIKE for simple contains match
            # We'll surround with quotes to match whole tag tokens, but also handle loose contains
            where_clauses.append("(LOWER(tags) LIKE LOWER(?) OR LOWER(tags) LIKE LOWER(?))")
            params.extend([f'%"{tag_filter}%', f'%{tag_filter}%'])
        
        where_clause = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

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
        cursor.execute(count_query, params[:-2] if where_clauses else [])
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

@app.route('/api/workouts/<int:workout_id>/tags', methods=['GET', 'PUT'])
def workout_tags(workout_id: int):
    try:
        conn = get_db_connection()
        _ensure_workout_tags_column(conn)
        cur = conn.cursor()
        # Ensure workout exists
        cur.execute('SELECT id, tags FROM workouts WHERE id = ?', (workout_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Workout not found'}), 404

        if request.method == 'GET':
            raw = row['tags'] if isinstance(row, sqlite3.Row) else row[1]
            try:
                tags = json.loads(raw) if raw else []
            except Exception:
                tags = []
            conn.close()
            return jsonify({'workout_id': workout_id, 'tags': tags})

        # PUT - set tags array entirely
        data = request.get_json(silent=True) or {}
        tags = data.get('tags')
        if not isinstance(tags, list):
            conn.close()
            return jsonify({'error': 'tags must be a list of strings'}), 400
        # Normalize and dedupe
        cleaned = []
        seen = set()
        for t in tags:
            if not isinstance(t, str):
                continue
            s = t.strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(s)
        tags_json = json.dumps(cleaned)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur.execute('UPDATE workouts SET tags = ?, updated_at = ? WHERE id = ?', (tags_json, now_iso, workout_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Tags updated', 'workout_id': workout_id, 'tags': cleaned})
    except Exception as e:
        logger.error(f"Error handling tags for workout {workout_id}: {e}")
        return jsonify({'error': 'Failed to handle tags'}), 500

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

@app.route('/api/workouts/<int:workout_id>/rename', methods=['PATCH'])
def rename_workout(workout_id: int):
    try:
        data = request.get_json(silent=True) or {}
        new_name = data.get('name')
        if new_name is None:
            return jsonify({'error': 'Missing name'}), 400
        # Normalize name: strip whitespace and limit length
        new_name = str(new_name).strip()
        if len(new_name) == 0:
            return jsonify({'error': 'Name cannot be empty'}), 400
        if len(new_name) > 200:
            new_name = new_name[:200]

        conn = get_db_connection()
        cur = conn.cursor()

        # Ensure workout exists
        cur.execute('SELECT id FROM workouts WHERE id = ?', (workout_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Workout not found'}), 404

        # Update name and updated_at
        now_iso = datetime.now(timezone.utc).isoformat()
        cur.execute('UPDATE workouts SET name = ?, updated_at = ? WHERE id = ?', (new_name, now_iso, workout_id))
        conn.commit()
        conn.close()

        return jsonify({'message': 'Workout renamed', 'workout_id': workout_id, 'name': new_name})
    except Exception as e:
        logger.error(f"Error renaming workout {workout_id}: {e}")
        return jsonify({'error': 'Failed to rename workout'}), 500

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


@app.route('/api/monthly-summary', methods=['GET'])
def get_monthly_summary():
    try:
        user_tz = detect_user_timezone()
        now_local = datetime.now(user_tz)
        # Start of current month
        start_of_month_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Start of next month
        if start_of_month_local.month == 12:
            start_of_next_month_local = start_of_month_local.replace(year=start_of_month_local.year + 1, month=1)
        else:
            start_of_next_month_local = start_of_month_local.replace(month=start_of_month_local.month + 1)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('SELECT start_time, duration_seconds, distance_meters, total_calories, processed FROM workouts')
        rows = cur.fetchall()
        conn.close()

        workouts_count = 0
        processed_count = 0
        total_distance_m = 0.0
        total_duration_s = 0
        total_calories = 0
        daily_counts = {}

        for row in rows:
            start_time_str = row[0]
            if not start_time_str:
                continue
            dt = parse_timestamp_with_timezone(start_time_str)
            if not dt:
                continue
            dt_local = convert_utc_to_local(dt, user_tz)
            if start_of_month_local <= dt_local < start_of_next_month_local:
                workouts_count += 1
                if row[4]:
                    processed_count += 1
                if row[2] is not None:
                    try:
                        total_distance_m += float(row[2])
                    except (TypeError, ValueError):
                        pass
                if row[1] is not None:
                    try:
                        total_duration_s += int(row[1])
                    except (TypeError, ValueError):
                        pass
                if row[3] is not None:
                    try:
                        total_calories += int(row[3])
                    except (TypeError, ValueError):
                        pass
                dkey = dt_local.date().isoformat()
                daily_counts[dkey] = daily_counts.get(dkey, 0) + 1

        # Build ordered daily list for all days in the month
        daily = []
        day_cursor = start_of_month_local
        while day_cursor < start_of_next_month_local:
            dkey = day_cursor.date().isoformat()
            daily.append({'date': dkey, 'count': daily_counts.get(dkey, 0)})
            day_cursor = day_cursor + timedelta(days=1)

        return jsonify({
            'month_start': start_of_month_local.isoformat(),
            'month_end': start_of_next_month_local.isoformat(),
            'workouts_count': workouts_count,
            'processed_count': processed_count,
            'total_distance_m': total_distance_m,
            'total_duration_s': total_duration_s,
            'total_calories': total_calories,
            'daily_counts': daily
        })
    except Exception as e:
        logger.error(f"Error computing monthly summary: {str(e)}")
        return jsonify({'error': 'Failed to compute monthly summary'}), 500

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
    """Return a themed Folium map HTML string matching page light/dark styles."""
    # Fallback page when no points are available
    if not points:
        is_dark = (theme == 'dark')
        bg = '#0b1220' if is_dark else '#f8fafc'  # Tailwind-ish gray-950 / slate-50
        fg = '#e5e7eb' if is_dark else '#1f2937'
        panel_bg = 'rgba(17,24,39,0.7)' if is_dark else 'rgba(255,255,255,0.9)'
        border = 'rgba(75,85,99,0.6)' if is_dark else 'rgba(229,231,235,0.7)'
        css = (
            "html, body { height:100%; margin:0; background:%s; color:%s; font-family: system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,sans-serif; }"
            " .box { display:flex; align-items:center; justify-content:center; height:100vh; }"
            " .note { background:%s; border:1px solid %s; padding:10px 12px; border-radius:10px; backdrop-filter: blur(6px); }"
        ) % (bg, fg, panel_bg, border)
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<style>{css}</style></head><body><div class='box'><div class='note'>No GPS data available</div></div></body></html>"
        )

    # Compute bounds/center
    lats = [p['lat'] for p in points]
    lons = [p['lon'] for p in points]
    center = (sum(lats) / len(lats), sum(lons) / len(lons))

    # Theme settings
    mode = 'dark' if theme == 'dark' else 'light'
    tiles = 'CartoDB dark_matter' if mode == 'dark' else 'CartoDB positron'
    line_color = '#60a5fa' if mode == 'dark' else '#3b82f6'  # Tailwind blue-400/500

    # Base map with explicit tile layer for control over filters
    m = folium.Map(location=center, tiles=None, zoom_start=14, control_scale=True)
    folium.TileLayer(tiles=tiles, name='Base', control=False).add_to(m)

    # Path
    coords = [(p['lat'], p['lon']) for p in points]
    folium.PolyLine(coords, color=line_color, weight=4, opacity=0.9).add_to(m)

    # Start/finish markers (minimal circle markers)
    start = coords[0]
    end = coords[-1]
    start_fill = '#34d399' if mode == 'dark' else '#10b981'  # emerald-400/green-600
    end_fill = '#f87171' if mode == 'dark' else '#ef4444'    # red-400/red-500
    folium.CircleMarker(location=start, radius=6, color='#ffffff', weight=2, fill=True,
                        fill_color=start_fill, fill_opacity=0.95, tooltip='Start').add_to(m)
    folium.CircleMarker(location=end, radius=6, color='#ffffff', weight=2, fill=True,
                        fill_color=end_fill, fill_opacity=0.95, tooltip='Finish').add_to(m)

    # Fit to bounds
    m.fit_bounds([(min(lats), min(lons)), (max(lats), max(lons))], padding=(20, 20))

    # Inject CSS to align Leaflet controls with page theme
    html = m.get_root().render()
    is_dark = (mode == 'dark')
    panel_bg = 'rgba(17,24,39,0.7)' if is_dark else 'rgba(255,255,255,0.9)'
    border = 'rgba(75,85,99,0.6)' if is_dark else 'rgba(229,231,235,0.7)'
    text = '#e5e7eb' if is_dark else '#1f2937'
    hover = 'rgba(31,41,55,0.7)' if is_dark else 'rgba(243,244,246,0.9)'
    tile_filter = 'brightness(0.82) contrast(1.05) saturate(0.9)' if is_dark else 'none'
    extra_css = f"""
<style>
  html, body {{ background: transparent; }}
  .leaflet-container {{ background: transparent; }}
  .leaflet-bar a, .leaflet-bar a:hover {{ color: {text}; }}
  .leaflet-control-zoom, .leaflet-control-attribution, .leaflet-control-scale {{
    background: {panel_bg};
    border: 1px solid {border};
    border-radius: 10px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
    backdrop-filter: blur(6px);
  }}
    /* Ensure attribution control background is themed (override Leaflet defaults) */
    .leaflet-container .leaflet-control-attribution {{
        background: {panel_bg} !important;
        border: 1px solid {border} !important;
        color: {text} !important;
        border-radius: 10px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.2);
        backdrop-filter: blur(6px);
    }}
    .leaflet-container .leaflet-control-attribution a {{
        color: {text} !important;
        background: transparent !important;
    }}
    /* Scale control inner lines */
    .leaflet-control-scale .leaflet-control-scale-line {{
        background: {panel_bg};
        color: {text};
        border-color: {border};
        box-shadow: none;
    }}
    .leaflet-control-scale .leaflet-control-scale-line:not(:first-child) {{
        border-top-color: {border};
    }}
  .leaflet-control-zoom a {{
    background: transparent;
    border-bottom: 1px solid {border};
  }}
  .leaflet-control-zoom a:last-child {{ border-bottom: none; }}
  .leaflet-bar a:hover {{ background: {hover}; }}
  .leaflet-control-attribution {{ color: {text}; }}
  .leaflet-control-attribution a {{ color: inherit; text-decoration: underline; }}
  .leaflet-tile {{ filter: {tile_filter}; }}
</style>
"""
    if '</head>' in html:
        html = html.replace('</head>', extra_css + '</head>')
    else:
        html = extra_css + html
    return html

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
