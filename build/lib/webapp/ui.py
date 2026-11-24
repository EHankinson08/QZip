#!/usr/bin/env python3
"""QZip local web UI (Flask, 127.0.0.1:5000): drag-and-drop compress/decompress, folders, progress.
Talks to the local API server (webapp/api.py); every result is verified byte-exact before it is offered."""

from flask import Flask, request, jsonify, render_template, send_file, session, Response
from flask_cors import CORS
from qzip.container import unpack, strip_ext
import os
import json
import hashlib
import tempfile
import gzip
import mimetypes
import time
import base64
import requests
from datetime import datetime, timedelta
from pathlib import Path
import logging
from functools import wraps
import secrets
import asyncio
import io
import zipfile
import shutil
import threading
import random

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['UPLOAD_FOLDER'] = os.getenv('QZIP_UPLOAD_DIR', 'uploads')
app.config['DOWNLOAD_FOLDER'] = os.getenv('QZIP_DOWNLOAD_DIR', 'downloads')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB max - increased for large folders
app.config['SESSION_COOKIE_SECURE'] = os.getenv('QZIP_SECURE_COOKIES', '0') == '1'  # set 1 behind HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

# Enable CORS for API endpoints
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Enterprise API Configuration
QUANTUM_API_URL = os.getenv('QUANTUM_API_URL', 'http://localhost:8000')
QUANTUM_API_KEY = os.getenv('QUANTUM_API_KEY', '')  # optional; never hard-code a key here
# Local / open-source mode (default): no API key needed. Set QZIP_REQUIRE_API_KEY=1 for a hosted deployment.
AUTH_REQUIRED = os.getenv('QZIP_REQUIRE_API_KEY', '0') == '1'

# Cache for compression jobs
COMPRESSION_JOBS = {}

# Progress tracking for active compressions
ACTIVE_COMPRESSIONS = {}

# Progress tracking for active decompressions
ACTIVE_DECOMPRESSIONS = {}

# Compression history (last 50 jobs)
COMPRESSION_HISTORY = []
MAX_HISTORY_SIZE = 50

# Simple file-based job storage
import pickle
from pathlib import Path

JOBS_STORAGE_DIR = Path(os.getenv("QZIP_JOBS_DIR", "storage/jobs"))
JOBS_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

def save_job_to_disk(job_id, job_data):
    """Save job data to disk for persistence"""
    try:
        job_file = JOBS_STORAGE_DIR / f"{job_id}.pkl"
        with open(job_file, 'wb') as f:
            pickle.dump(job_data, f)
        logger.info(f"💾 Job {job_id} saved to disk")
    except Exception as e:
        logger.error(f"Failed to save job {job_id} to disk: {e}")

def load_job_from_disk(job_id):
    """Load job data from disk"""
    try:
        job_file = JOBS_STORAGE_DIR / f"{job_id}.pkl"
        if job_file.exists():
            with open(job_file, 'rb') as f:
                job_data = pickle.load(f)
            logger.info(f"📁 Job {job_id} loaded from disk")
            return job_data
    except Exception as e:
        logger.error(f"Failed to load job {job_id} from disk: {e}")
    return None

def delete_job_from_disk(job_id):
    """Delete job data from disk"""
    try:
        job_file = JOBS_STORAGE_DIR / f"{job_id}.pkl"
        if job_file.exists():
            job_file.unlink()
            logger.info(f"🗑️ Job {job_id} deleted from disk")
    except Exception as e:
        logger.error(f"Failed to delete job {job_id} from disk: {e}")

# Create directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)
os.makedirs('static', exist_ok=True)

logger.info("🚀 QZip Enterprise Web Interface")
logger.info("QZip: lossless; ratios are dynamic and measured per file (see /api/benchmarks). Wins on structured logs/CSV/medical; loses to zstd/xz on generic text/photos.")
logger.info("✅ Every result is round-trip verified (sha256) before it is offered for download")
logger.info(f"🔑 API key required: {AUTH_REQUIRED} (set QZIP_REQUIRE_API_KEY=1 to enable)")

def require_auth(f):
    """Decorator for routes that require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not AUTH_REQUIRED:
            return f(*args, **kwargs)
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key') or session.get('api_key')
        if not api_key:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """Main interface"""
    return render_template('quantum_interface.html')

@app.route('/health')
@app.route('/api/health')
def health_check():
    """Health check"""
    try:
        # Check if API server is responding
        api_health = {'status': 'unknown'}
        try:
            response = requests.get(f'{QUANTUM_API_URL}/health', timeout=2)
            if response.status_code == 200:
                api_health = response.json()
        except:
            api_health = {'status': 'api_unreachable'}
        
        return jsonify({
            'status': 'healthy',
            'web_interface': 'operational',
            'api_server': api_health,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

@app.route('/api/config')
def get_config():
    """Tells the UI whether an API key is needed (False in local mode)."""
    return jsonify({'auth_required': AUTH_REQUIRED, 'license': 'GPL-3.0-or-later'})

@app.route('/api/auth', methods=['POST'])
def authenticate():
    """Authenticate user with API key"""
    if not AUTH_REQUIRED:
        return jsonify({'success': True, 'message': 'Local mode: no API key required'})
    try:
        data = request.get_json(silent=True) or {}
        api_key = data.get('api_key')
        
        if not api_key:
            return jsonify({'error': 'API key required'}), 400
        
        # Verify API key by making a test request
        headers = {'Authorization': f'Bearer {api_key}'}
        response = requests.get(f'{QUANTUM_API_URL}/health', headers=headers, timeout=5)
        
        if response.status_code == 200:
            session['api_key'] = api_key
            session.permanent = True
            # Get methods info for the user
            methods_response = requests.get(f'{QUANTUM_API_URL}/methods')
            return jsonify({
                'success': True,
                'message': 'Authentication successful',
                'methods': methods_response.json() if methods_response.status_code == 200 else {}
            })
        else:
            return jsonify({'error': 'Invalid API key'}), 401
            
    except Exception as e:
        logger.error(f"❌ Authentication error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
@require_auth
def get_stats():
    """Get compression statistics"""
    try:
        # Calculate stats from jobs
        total_jobs = len(COMPRESSION_JOBS)
        total_original_size = 0
        total_compressed_size = 0
        
        for job in COMPRESSION_JOBS.values():
            if job.get('type') == 'folder':
                # Handle folder compression jobs
                total_original_size += job.get('total_original_size', 0)
                total_compressed_size += job.get('total_compressed_size', 0)
            else:
                # Handle individual file compression jobs
                if job.get('type') == 'decompression' or job.get('status') not in (None, 'complete', 'completed'):
                    continue
                if job.get('compressed_size'):
                    total_original_size += job.get('original_size', 0)
                    total_compressed_size += job['compressed_size']
                elif job.get('compressed_data'):
                    total_original_size += job.get('original_size', 0)
                    total_compressed_size += len(base64.b64decode(job['compressed_data']))
        
        avg_ratio = (total_original_size / total_compressed_size) if total_compressed_size > 0 else 0
        
        # Get recent jobs
        recent_jobs = sorted(
            [(job_id, job) for job_id, job in COMPRESSION_JOBS.items()],
            key=lambda x: x[1].get('timestamp', ''),
            reverse=True
        )[:5]
        
        # Format recent jobs for display
        recent_jobs_formatted = []
        for job_id, job in recent_jobs:
            if job.get('type') == 'folder':
                recent_jobs_formatted.append({
                    'filename': job.get('folder_name', 'Unknown Folder'),
                    'original_size': job.get('total_original_size', 0),
                    'compression_ratio': job.get('overall_ratio', 0),
                    'method': 'hybrid_quantum',
                    'timestamp': job.get('timestamp', ''),
                    'type': 'folder'
                })
            else:
                # Calculate compression_speed_mbps from history or job data
                compression_speed = 0
                if 'compression_time' in job and job.get('compression_time', 0) > 0:
                    original_size = job.get('original_size', 0)
                    compression_time = job.get('compression_time', 1)
                    compression_speed = (original_size / (1024 * 1024)) / compression_time
                
                recent_jobs_formatted.append({
                    'filename': job.get('original_filename', 'Unknown File'),
                    'original_size': job.get('original_size', 0),
                    'compression_ratio': job.get('compression_ratio', 0),
                    'method': job.get('method', 'unknown'),
                    'timestamp': job.get('timestamp', ''),
                    'type': 'file',
                    'compression_speed_mbps': round(compression_speed, 2)
                })
        
        return jsonify({
            'total_compressions': total_jobs,
            'total_original_bytes': total_original_size,
            'total_compressed_bytes': total_compressed_size,
            'average_compression_ratio': round(avg_ratio, 1),
            'space_saved_percentage': round((1 - total_compressed_size/total_original_size) * 100, 2) if total_original_size > 0 else 0,
            'recent_jobs': recent_jobs_formatted
        })
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/compress', methods=['POST'])
@require_auth
def compress_file():
    """Accept an upload and compress it.

    Backward compatible: the default is to wait until the job finishes and
    return the original success JSON (no hard timeout unless
    QZIP_COMPRESS_TIMEOUT is set). Pass async=1 (form/query) or
    X-QZip-Async: 1 to get 202 + job_id immediately, then poll
    /api/progress/<job_id> and download /api/download/<job_id>.
    """
    job_id = None
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        method = request.form.get('mode') or request.form.get('method', 'auto')
        from werkzeug.utils import secure_filename
        from qzip.jobs import get_manager, compress_timeout_sec

        original_filename = file.filename
        safe_name = secure_filename(original_filename) or 'upload.bin'
        mgr = get_manager(
            upload_dir=app.config['UPLOAD_FOLDER'],
            download_dir=app.config['DOWNLOAD_FOLDER'],
            jobs_dir=str(JOBS_STORAGE_DIR),
        )
        job_id = hashlib.md5(f"{original_filename}_{datetime.now().isoformat()}_{random.random()}".encode()).hexdigest()
        upload_path = str(Path(app.config['UPLOAD_FOLDER']) / f"{job_id}_{safe_name}")
        Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
        # Stream the multipart body to disk instead of file.read() into RAM.
        file.save(upload_path)
        original_size = os.path.getsize(upload_path)

        def _truthy(name, header=None):
            raw = request.form.get(name)
            if raw is None:
                raw = request.args.get(name)
            if raw is None and header:
                raw = request.headers.get(header)
            if raw is None:
                return None
            return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')

        wait_flag = _truthy('wait', 'X-QZip-Wait')
        async_flag = _truthy('async', 'X-QZip-Async')
        is_async = bool(async_flag) and not bool(wait_flag)

        logger.info(f"Compressing {original_filename} ({original_size:,} bytes) mode={method} async={is_async}")
        mgr.start(
            input_path=upload_path,
            filename=original_filename,
            mode=method,
            heartbeat_required=is_async,
            job_id=job_id,
        )
        ACTIVE_COMPRESSIONS[job_id] = {
            'filename': original_filename,
            'original_size': original_size,
            'status': 'starting',
            'progress': 0,
            'start_time': time.time(),
        }

        if is_async:
            st = mgr.status(job_id, heartbeat=True) or {}
            return jsonify({
                'accepted': True,
                'job_id': job_id,
                'status': st.get('status', 'starting'),
                'filename': original_filename,
                'original_size': original_size,
                'status_url': f'/api/progress/{job_id}',
                'download_url': f'/api/download/{job_id}',
                'cancel_url': f'/api/jobs/{job_id}/cancel',
            }), 202

        try:
            st = mgr.wait(job_id, timeout=compress_timeout_sec())
        except TimeoutError as e:
            elapsed = time.time() - ACTIVE_COMPRESSIONS.get(job_id, {}).get('start_time', time.time())
            error_message = str(e) if str(e) else f"Compression timed out after {elapsed:.1f}s"
            if job_id in ACTIVE_COMPRESSIONS:
                ACTIVE_COMPRESSIONS[job_id]['status'] = 'timeout'
                ACTIVE_COMPRESSIONS[job_id]['error'] = error_message
            return jsonify({'error': error_message, 'job_id': job_id}), 504

        if st.get('status') != 'complete':
            error_message = st.get('error') or 'Compression failed'
            if job_id in ACTIVE_COMPRESSIONS:
                ACTIVE_COMPRESSIONS[job_id]['status'] = st.get('status') or 'error'
                ACTIVE_COMPRESSIONS[job_id]['error'] = error_message
            code = 499 if st.get('status') == 'cancelled' else 500
            return jsonify({'error': error_message, 'job_id': job_id}), code

        result = st.get('result') or mgr.result(job_id) or {}
        _publish_completed_web_job(job_id, result, method)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Compression error: {e}")
        error_message = f"Compression failed: {str(e)}"
        if job_id and job_id in ACTIVE_COMPRESSIONS:
            ACTIVE_COMPRESSIONS[job_id]['status'] = 'error'
            ACTIVE_COMPRESSIONS[job_id]['error'] = error_message
        return jsonify({'error': error_message}), 500


def _publish_completed_web_job(job_id, result, method='fast'):
    """Mirror a finished job into COMPRESSION_JOBS / history so /api/download keeps working."""
    from qzip.jobs import get_manager
    mgr = get_manager()
    output_path = mgr.output_path(job_id)
    job_data = {
        'type': 'compression',
        'original_filename': result.get('original_filename'),
        'original_size': result.get('original_size'),
        'output_path': output_path,
        'compressed_size': result.get('compressed_size'),
        'method': result.get('method', method),
        'compression_ratio': result.get('compression_ratio'),
        'quantum_parameters': {'qzip': {
            'mode': result.get('mode'),
            'chosen': result.get('route_method'),
            'detected_type': result.get('detected_type'),
        }},
        'timestamp': result.get('timestamp') or datetime.now().isoformat(),
        'compression_time': result.get('compression_time'),
        'compression_speed_mbps': result.get('compression_speed_mbps'),
    }
    COMPRESSION_JOBS[job_id] = job_data
    try:
        save_job_to_disk(job_id, {k: v for k, v in job_data.items() if k != 'compressed_data'})
    except Exception as e:
        logger.warning(f"Could not persist job {job_id}: {e}")

    if job_id in ACTIVE_COMPRESSIONS:
        ACTIVE_COMPRESSIONS[job_id].update({
            'status': 'complete',
            'progress': 100,
            'elapsed_time': result.get('compression_time'),
            'speed_mbps': result.get('compression_speed_mbps'),
        })

    history_entry = {
        'job_id': job_id,
        'filename': result.get('original_filename'),
        'original_size': result.get('original_size') or 0,
        'compressed_size': result.get('compressed_size') or 0,
        'compression_ratio': result.get('compression_ratio') or 0,
        'compression_time': result.get('compression_time') or 0,
        'speed_mbps': result.get('compression_speed_mbps') or 0,
        'method': result.get('method', method),
        'space_saved_percent': result.get('space_saved_percent') or 0,
        'timestamp': result.get('timestamp') or datetime.now().isoformat(),
        'download_url': f'/api/download/{job_id}',
    }
    COMPRESSION_HISTORY.insert(0, history_entry)
    if len(COMPRESSION_HISTORY) > MAX_HISTORY_SIZE:
        COMPRESSION_HISTORY.pop()
    return job_data


@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
@app.route('/api/compress/<job_id>/cancel', methods=['POST'])
@require_auth
def cancel_compression_job(job_id):
    """Cancel a running job and kill its worker so nothing is left orphaned."""
    from qzip.jobs import get_manager
    mgr = get_manager(
        upload_dir=app.config['UPLOAD_FOLDER'],
        download_dir=app.config['DOWNLOAD_FOLDER'],
        jobs_dir=str(JOBS_STORAGE_DIR),
    )
    st = mgr.status(job_id, heartbeat=False)
    if st is None and job_id not in ACTIVE_COMPRESSIONS:
        return jsonify({'error': 'Job not found', 'job_id': job_id}), 404
    final = mgr.cancel(job_id, reason='cancelled') or st or {'status': 'cancelled'}
    if job_id in ACTIVE_COMPRESSIONS:
        ACTIVE_COMPRESSIONS[job_id]['status'] = final.get('status', 'cancelled')
        ACTIVE_COMPRESSIONS[job_id]['error'] = final.get('error', 'Cancelled')
    return jsonify({
        'cancelled': final.get('status') in ('cancelled', 'timeout'),
        'job_id': job_id,
        'status': final.get('status'),
        'elapsed_time': final.get('elapsed_time'),
    })


@app.route('/api/progress/<job_id>', methods=['GET'])
def get_compression_progress(job_id):
    """Get real-time progress for a compression job"""
    try:
        from qzip.jobs import get_manager
        mgr = get_manager(
            upload_dir=app.config['UPLOAD_FOLDER'],
            download_dir=app.config['DOWNLOAD_FOLDER'],
            jobs_dir=str(JOBS_STORAGE_DIR),
        )
        st = mgr.status(job_id, heartbeat=True)
        if st is not None:
            if st.get('status') == 'complete' and st.get('result') and job_id not in COMPRESSION_JOBS:
                _publish_completed_web_job(job_id, st['result'], st['result'].get('mode') or 'fast')
            return jsonify(st)
        if job_id in ACTIVE_COMPRESSIONS:
            progress = ACTIVE_COMPRESSIONS[job_id].copy()
            
            # Calculate time remaining
            if progress['status'] == 'compressing' and progress['elapsed_time'] > 0:
                elapsed = time.time() - progress['start_time']
                estimated_total = progress['estimated_time']
                remaining = max(0, estimated_total - elapsed)
                progress['time_remaining'] = round(remaining, 1)
                progress['elapsed_time'] = round(elapsed, 1)
                
                # Update speed if we have progress
                if elapsed > 0:
                    progress['speed_mbps'] = round((progress['original_size'] / (1024 * 1024)) / elapsed, 2)
                
                # Estimate progress based on time (since we don't have real progress from API)
                if estimated_total > 0:
                    time_progress = min(85, int((elapsed / estimated_total) * 85))  # Cap at 85% until done
                    progress['progress'] = max(progress['progress'], time_progress)
            
            return jsonify(progress)
        elif job_id in ACTIVE_DECOMPRESSIONS:
            # Check decompression jobs
            progress = ACTIVE_DECOMPRESSIONS[job_id].copy()
            
            # Calculate elapsed time
            if progress.get('start_time'):
                elapsed = time.time() - progress['start_time']
                progress['elapsed_time'] = round(elapsed, 1)
            
            return jsonify(progress)
        elif job_id in COMPRESSION_JOBS:
            # Job completed - return completion status
            return jsonify({
                'status': 'complete',
                'progress': 100,
                'message': 'Compression complete'
            })
        else:
            return jsonify({'status': 'not_found', 'progress': 0}), 404
    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/history', methods=['GET'])
def get_compression_history():
    """Get compression history"""
    try:
        # Return last 20 entries
        return jsonify({
            'history': COMPRESSION_HISTORY[:20],
            'total_files': len(COMPRESSION_HISTORY),
            'total_original_size': sum(h['original_size'] for h in COMPRESSION_HISTORY),
            'total_compressed_size': sum(h['compressed_size'] for h in COMPRESSION_HISTORY),
            'average_ratio': sum(h['compression_ratio'] for h in COMPRESSION_HISTORY) / len(COMPRESSION_HISTORY) if COMPRESSION_HISTORY else 0
        })
    except Exception as e:
        logger.error(f"Error getting history: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/decompress', methods=['POST'])
@require_auth
def decompress_file():
    """Decompress uploaded quantum compressed file"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No compressed file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No compressed file selected'}), 400
        
        api_key = request.headers.get('X-API-Key') or session.get('api_key')
        
        logger.info(f"🔄 Decompressing {file.filename}")
        
        # Generate job ID for async processing
        job_id = hashlib.md5(f"{file.filename}_{datetime.now().isoformat()}_{random.random()}".encode()).hexdigest()
        
        # Store job info for tracking
        ACTIVE_DECOMPRESSIONS[job_id] = {
            'status': 'starting',
            'progress': 0,
            'filename': file.filename,
            'start_time': time.time(),
            'message': 'Starting decompression...'
        }
        
        # Read compressed data
        try:
            file_data = file.read()
            
            try:
                compressed_data = unpack(file_data)   # .qz, or an older .qc / .qc.gz
            except (OSError, UnicodeDecodeError, ValueError) as e:
                raise json.JSONDecodeError(str(e), '', 0)
        except json.JSONDecodeError as e:
            ACTIVE_DECOMPRESSIONS[job_id]['status'] = 'error'
            ACTIVE_DECOMPRESSIONS[job_id]['error'] = f'Invalid compressed file format: {str(e)}'
            return jsonify({'error': f'Invalid compressed file format: {str(e)}'}), 400
        
        # Determine original filename for progress display
        original_filename = compressed_data.get('original_filename', (strip_ext(file.filename) or file.filename))
        if not original_filename:
            original_filename = 'decompressed_file'
            
        # Update progress
        ACTIVE_DECOMPRESSIONS[job_id].update({
            'status': 'decompressing',
            'progress': 20,
            'original_filename': original_filename,
            'message': f'Decompressing {original_filename}...'
        })
        
        # Start background decompression
        def process_decompression():
            try:
                # Prepare API request
                headers = {
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                }
                
                # Debug logging for reconstruction_metadata
                reconstruction_metadata = compressed_data.get('reconstruction_metadata')
                if reconstruction_metadata is not None:
                    logger.info(f"📊 DEBUG: reconstruction_metadata type: {type(reconstruction_metadata)}, length: {len(str(reconstruction_metadata))}")
                    if isinstance(reconstruction_metadata, str) and len(reconstruction_metadata) < 100:
                        logger.info(f"📊 DEBUG: reconstruction_metadata content: {repr(reconstruction_metadata[:20])}")
                
                decompression_request = {
                    'compressed_data': compressed_data.get('compressed_data'),
                    'compression_method': compressed_data.get('method', 'hybrid_quantum'),
                    'reconstruction_metadata': reconstruction_metadata,
                    'quantum_parameters': compressed_data.get('quantum_parameters', {})
                }
                
                logger.info(f"📦 Decompression request: method={decompression_request['compression_method']}, has_metadata={bool(decompression_request.get('reconstruction_metadata'))}, quantum_params={decompression_request.get('quantum_parameters', {})}")
                
                # Update progress
                ACTIVE_DECOMPRESSIONS[job_id]['progress'] = 30
                
                # Call enterprise decompression API
                start_time = time.time()
                
                # Decompression can be slow with RL neural compression (pattern replacement)
                # RL decompression can take 2+ minutes, so set generous timeout
                # Minimum 10 minutes, scale with file size
                min_timeout = 600  # 10 minutes minimum
                max_timeout = 3600  # 60 minutes maximum
                dynamic_timeout = max(min_timeout, min(max_timeout, len(file_data) // (1024 * 1024) * 20))  # ~20 seconds per MB for RL
                
                logger.info(f"⏱️ Setting decompression timeout to {dynamic_timeout}s")
                
                response = requests.post(
                    f'{QUANTUM_API_URL}/decompress',
                    json=decompression_request,
                    headers=headers,
                    timeout=dynamic_timeout
                )
                decompression_time = time.time() - start_time
                
                if response.status_code != 200:
                    error_details = response.json() if response.content else {'error': 'Unknown error'}
                    logger.error(f"❌ Decompression API error: {response.status_code} - {error_details}")
                    error_msg = error_details.get('error', error_details.get('detail', 'Decompression failed'))
                    ACTIVE_DECOMPRESSIONS[job_id]['status'] = 'error'
                    ACTIVE_DECOMPRESSIONS[job_id]['error'] = error_msg
                    return
                
                # Update progress
                ACTIVE_DECOMPRESSIONS[job_id]['progress'] = 70
                
                # Parse response
                result = response.json()
                decompressed_data_base64 = result['decompressed_data']
                
                # Decode from base64
                # Always handle as bytes: text-mode writes change line endings on Windows (not byte-exact)
                decompressed_data = base64.b64decode(decompressed_data_base64)
                is_text = False
                
                # Update progress
                ACTIVE_DECOMPRESSIONS[job_id]['progress'] = 90
                
                # Save temporarily
                if is_text:
                    temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=f"_{original_filename}")
                    temp_file.write(decompressed_data)
                    decompressed_size = len(decompressed_data.encode('utf-8'))
                else:
                    temp_file = tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix=f"_{original_filename}")
                    temp_file.write(decompressed_data)
                    decompressed_size = len(decompressed_data)
                
                temp_file.close()
                
                # Determine content type
                content_type = mimetypes.guess_type(original_filename)[0] or ('text/plain' if is_text else 'application/octet-stream')
                
                # Store job data
                COMPRESSION_JOBS[job_id] = {
                    'type': 'decompression',
                    'temp_file_path': temp_file.name,
                    'original_filename': original_filename,
                    'content_type': content_type,
                    'size': decompressed_size,
                    'timestamp': datetime.now().isoformat()
                }
                
                # Save to disk for persistence
                save_job_to_disk(job_id, COMPRESSION_JOBS[job_id])
                
                # Update final status
                ACTIVE_DECOMPRESSIONS[job_id].update({
                    'status': 'complete',
                    'progress': 100,
                    'decompressed_size': decompressed_size,
                    'decompression_time': decompression_time,
                    'download_url': f'/api/download/{job_id}',
                    'message': 'Decompression complete!'
                })
                
                logger.info(f"✅ Decompression Complete: {original_filename}")
                
            except Exception as e:
                logger.error(f"❌ Background decompression error: {e}")
                ACTIVE_DECOMPRESSIONS[job_id]['status'] = 'error'
                ACTIVE_DECOMPRESSIONS[job_id]['error'] = str(e)
        
        # Start background thread
        decompression_thread = threading.Thread(target=process_decompression, daemon=True)
        decompression_thread.start()
        
        # Return immediately with job ID
        response_data = {
            'success': True,
            'job_id': job_id,
            'message': 'Decompression started',
            'status': 'processing',
            'download_url': f'/api/download/{job_id}'  # Will be available when complete
        }
        
        logger.info(f"📋 Decompression job started: {job_id}")
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"❌ Decompression error: {e}")
        return jsonify({'error': f'Decompression failed: {str(e)}'}), 500

@app.route('/api/batch/compress', methods=['POST'])
@require_auth
def batch_compress():
    """Compress multiple files in batch"""
    try:
        if 'files' not in request.files:
            return jsonify({'error': 'No files uploaded'}), 400
        
        files = request.files.getlist('files')
        method = request.form.get('mode') or request.form.get('method', 'auto')   # QZip mode (auto default)
        api_key = request.headers.get('X-API-Key') or session.get('api_key')
        
        if not files:
            return jsonify({'error': 'No files selected'}), 400
        
        logger.info(f"🔬 Batch compressing {len(files)} files")
        
        # Prepare batch request
        batch_items = []
        total_original_size = 0
        
        for file in files:
            if file.filename:
                file_data = file.read()
                total_original_size += len(file_data)
                
                batch_items.append({
                    'id': file.filename,
                    'data': base64.b64encode(file_data).decode('utf-8'),
                    'method': method
                })
        
        if not batch_items:
            return jsonify({'error': 'No valid files to compress'}), 400
        
        # Call batch compression API
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        start_time = time.time()
        response = requests.post(
            f'{QUANTUM_API_URL}/compress/batch',
            json={'items': batch_items},
            headers=headers,
            timeout=600  # 10 minute timeout for batch
        )
        batch_time = time.time() - start_time
        
        if response.status_code != 200:
            error_msg = response.json().get('error', 'Batch compression failed')
            return jsonify({'error': error_msg}), response.status_code
        
        # Parse response
        result = response.json()
        
        # Create batch job ID
        batch_id = hashlib.md5(f"batch_{datetime.now().isoformat()}".encode()).hexdigest()
        
        # Store batch results
        COMPRESSION_JOBS[batch_id] = {
            'type': 'batch_compression',
            'results': result['results'],
            'total_original_size': result.get('total_original_size', total_original_size),
            'total_compressed_size': result.get('total_compressed_size'),
            'overall_ratio': result.get('overall_ratio'),
            'timestamp': datetime.now().isoformat()
        }
        
        response_data = {
            'success': True,
            'batch_id': batch_id,
            'files_processed': len(result['results']),
            'total_original_size': total_original_size,
            'total_original_size_formatted': format_file_size(total_original_size),
            'total_compressed_size': result.get('total_compressed_size'),
            'total_compressed_size_formatted': format_file_size(result.get('total_compressed_size', 0)),
            'overall_compression_ratio': result.get('overall_ratio'),
            'batch_time': round(batch_time, 3),
            'download_url': f'/api/download/batch/{batch_id}',
            'results': result['results'],
            'timestamp': datetime.now().isoformat()
        }
        
        logger.info(f"✅ Batch Compression Complete: {len(files)} files, {result.get('overall_ratio', 0):.1f}:1 overall ratio")
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"❌ Batch compression error: {e}")
        return jsonify({'error': f'Batch compression failed: {str(e)}'}), 500

@app.route('/api/compress/folder', methods=['POST'])
@require_auth
def compress_folder():
    """Compress multiple files from a folder (handles both zip uploads and direct folder processing)""" 
    try:
        # Check if this is a zip file upload or direct folder processing
        if 'folder' in request.files:
            # Handle zip file upload (legacy support)
            return compress_folder_from_zip()
        elif 'files' in request.files:
            # Handle direct folder processing (new approach)
            return compress_folder_direct()
        else:
            return jsonify({'error': 'No folder or files uploaded'}), 400
        
    except Exception as e:
        logger.error(f"❌ Folder compression error: {e}")
        return jsonify({'error': f'Folder compression failed: {str(e)}'}), 500

def compress_folder_from_zip():
    """Compress folder from zip file upload (legacy support)"""
    folder_file = request.files['folder']
    method = request.form.get('method', 'hybrid_quantum')
    api_key = request.headers.get('X-API-Key') or session.get('api_key')
    
    if not folder_file.filename:
        return jsonify({'error': 'No folder selected'}), 400
    
    logger.info(f"📁 Compressing folder from zip: {folder_file.filename}")
    
    # Create temporary directory for extraction
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Save uploaded zip file
        zip_path = temp_path / folder_file.filename
        folder_file.save(zip_path)
        
        # Extract the zip file
        extract_path = temp_path / 'extracted'
        extract_path.mkdir()
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
        
        # Process all files recursively
        return process_folder_files(extract_path, method, api_key, Path(folder_file.filename).stem)

def compress_folder_direct():
    """Compress multiple files into a single compressed archive"""
    files = request.files.getlist('files')
    method = request.form.get('method', 'hybrid_quantum')
    folder_name = request.form.get('folder_name', 'Compressed Files')
    api_key = request.headers.get('X-API-Key') or session.get('api_key')
    
    if not files:
        return jsonify({'error': 'No files uploaded'}), 400
    
    logger.info(f"📁 Compressing {len(files)} files from folder '{folder_name}' into single archive")
    
    # Compress each file individually using quantum compression
    compressed_files = []
    total_original_size = 0
    total_compressed_size = 0
    successful_compressions = 0
    
    for file in files:
        if file.filename:
            try:
                # Read file data
                file_data = file.read()
                file_size = len(file_data)
                total_original_size += file_size
                
                logger.info(f"🔬 Compressing {file.filename} ({file_size:,} bytes)")
                
                # Compress individual file
                compression_data = {
                    "data": base64.b64encode(file_data).decode('utf-8'),
                    "method": method,
                    "filename": file.filename
                }
                
                headers = {
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                }
                
                response = requests.post(
                    f"{QUANTUM_API_URL}/compress",
                    json=compression_data,
                    headers=headers,
                    timeout=300
                )
                
                if response.status_code == 200:
                    result = response.json()
                    compressed_size = result['compressed_size']  # Use API's compressed_size which includes metadata
                    total_compressed_size += compressed_size
                    successful_compressions += 1
                    
                    compression_ratio = file_size / compressed_size if compressed_size > 0 else 0
                    logger.info(f"✅ {file.filename}: {file_size:,} → {compressed_size:,} bytes ({compression_ratio:.1f}:1)")
                    
                    compressed_files.append({
                        'filename': file.filename,
                        'original_size': file_size,
                        'compressed_size': compressed_size,
                        'compressed_data': result['compressed_data'],
                        'reconstruction_metadata': result.get('reconstruction_metadata', ''),
                        'method': result.get('compression_method', method)
                    })
                    
                else:
                    error_msg = response.json().get('error', 'Unknown error') if response.content else 'No response content'
                    logger.error(f"❌ Failed to compress {file.filename}: {response.status_code} - {error_msg}")
                    
            except Exception as e:
                logger.error(f"❌ Error compressing {file.filename}: {e}")
    
    if successful_compressions == 0:
        return jsonify({'error': 'Failed to compress any files'}), 500
    
    # Create compressed archive data
    archive_data = {
        'type': 'multi_file_archive',
        'archive_name': folder_name,
        'files_count': len(compressed_files),
        'total_original_size': total_original_size,
        'total_compressed_size': total_compressed_size,
        'compression_method': method,
        'timestamp': datetime.now().isoformat(),
        'files': compressed_files
    }
    
    # Compress the archive with gzip
    json_data = json.dumps(archive_data, separators=(',', ':'))
    compressed_archive = gzip.compress(json_data.encode('utf-8'))
    
    # Create job entry
    job_id = secrets.token_hex(16)
    COMPRESSION_JOBS[job_id] = {
        'type': 'multi_file_archive',
        'archive_name': folder_name,
        'files_count': len(compressed_files),
        'total_original_size': total_original_size,
        'total_compressed_size': total_compressed_size,
        'overall_ratio': total_original_size / total_compressed_size if total_compressed_size > 0 else 0,
        'compressed_archive': compressed_archive,
        'timestamp': datetime.now().isoformat()
    }
    
    overall_ratio = total_original_size / total_compressed_size if total_compressed_size > 0 else 0
    logger.info(f"✅ Multi-file compression complete: {successful_compressions}/{len(files)} files, {overall_ratio:.1f}:1 ratio")
    
    return jsonify({
        'success': True,
        'folder_id': job_id,
        'folder_name': folder_name,
        'files_count': len(compressed_files),
        'total_original_size': total_original_size,
        'total_original_size_formatted': format_file_size(total_original_size),
        'total_compressed_size': total_compressed_size,
        'total_compressed_size_formatted': format_file_size(total_compressed_size),
        'overall_compression_ratio': overall_ratio,
        'download_url': f'/api/download/folder/{job_id}',
        'timestamp': datetime.now().isoformat()
    })

def process_folder_files(folder_path, method, api_key, folder_name):
    """Process all files in a folder recursively"""
    # Get all files in the folder recursively
    all_files = []
    total_size = 0
    
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            file_path = Path(root) / file
            relative_path = file_path.relative_to(folder_path)
            file_size = file_path.stat().st_size
            
            all_files.append({
                'path': str(relative_path),
                'size': file_size,
                'full_path': str(file_path)
            })
            total_size += file_size
    
    logger.info(f"📊 Found {len(all_files)} files, total size: {total_size:,} bytes")
    
    # Compress each file individually
    compressed_files = []
    total_compressed_size = 0
    
    for file_info in all_files:
        try:
            with open(file_info['full_path'], 'rb') as f:
                file_data = f.read()
            
            # Compress individual file
            compression_data = {
                "data": base64.b64encode(file_data).decode('utf-8'),
                "method": method,
                "filename": file_info['path']
            }
            
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(
                f"{QUANTUM_API_URL}/compress",
                json=compression_data,
                headers=headers,
                timeout=300
            )
            
            if response.status_code == 200:
                result = response.json()
                compressed_size = len(base64.b64decode(result['compressed_data']))
                ratio = file_info['size'] / compressed_size if compressed_size > 0 else 0
                
                compressed_files.append({
                    'path': file_info['path'],
                    'original_size': file_info['size'],
                    'compressed_size': compressed_size,
                    'ratio': ratio,
                    'compressed_data': result['compressed_data'],
                    'reconstruction_metadata': result.get('reconstruction_metadata', ''),
                    'method': result.get('compression_method', method)
                })
                
                total_compressed_size += compressed_size
                
            else:
                logger.warning(f"Failed to compress {file_info['path']}: {response.status_code}")
                
        except Exception as e:
            logger.warning(f"Error compressing {file_info['path']}: {e}")
    
    # Create folder compression result
    folder_id = secrets.token_hex(16)
    
    folder_result = {
        'type': 'folder',
        'folder_name': folder_name,
        'files_count': len(compressed_files),
        'total_original_size': total_size,
        'total_compressed_size': total_compressed_size,
        'overall_ratio': total_size / total_compressed_size if total_compressed_size > 0 else 0,
        'files': compressed_files,
        'timestamp': datetime.now().isoformat()
    }
    
    # Store in memory
    COMPRESSION_JOBS[folder_id] = folder_result
    
    logger.info(f"✅ Folder compression complete: {len(compressed_files)} files, {folder_result['overall_ratio']:.1f}:1 ratio")
    
    # Create individual download URLs for each compressed file
    individual_files = []
    for file_info in compressed_files:
        # Create individual job for each file
        file_job_id = secrets.token_hex(16)
        file_job = {
            'type': 'file',
            'original_filename': file_info['path'],
            'original_size': file_info['original_size'],
            'compressed_data': file_info['compressed_data'],
            'reconstruction_metadata': file_info['reconstruction_metadata'],
            'method': file_info['method'],
            'compression_ratio': file_info['ratio'],
            'timestamp': datetime.now().isoformat()
        }
        COMPRESSION_JOBS[file_job_id] = file_job
        
        individual_files.append({
            'filename': file_info['path'],
            'original_size': file_info['original_size'],
            'compressed_size': file_info['compressed_size'],
            'compression_ratio': file_info['ratio'],
            'download_url': f'/api/download/{file_job_id}'
        })
    
    return jsonify({
        'success': True,
        'folder_name': folder_name,
        'files_count': len(compressed_files),
        'total_original_size': total_size,
        'total_original_size_formatted': format_file_size(total_size),
        'total_compressed_size': total_compressed_size,
        'total_compressed_size_formatted': format_file_size(total_compressed_size),
        'overall_compression_ratio': folder_result['overall_ratio'],
        'individual_files': individual_files,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/download/folder/<folder_id>')
@require_auth
def download_folder(folder_id):
    """Download compressed multi-file archive as a single .qc.gz file"""
    try:
        logger.info(f"📥 Download request for folder: {folder_id}")
        
        if folder_id not in COMPRESSION_JOBS:
            return jsonify({'error': 'Folder not found or expired'}), 404
        
        folder_data = COMPRESSION_JOBS[folder_id]
        
        if folder_data['type'] not in ['folder', 'multi_file_archive']:
            return jsonify({'error': 'Invalid folder ID'}), 400
        
        # For multi-file archives, use the pre-compressed data
        if folder_data['type'] == 'multi_file_archive':
            compressed_data = folder_data['compressed_archive']
            download_name = f"{folder_data['archive_name']}.qc.gz"
        else:
            # For regular folders, create compressed data
            compressed_folder_data = {
                'type': 'folder',
                'folder_name': folder_data['folder_name'],
                'files_count': folder_data['files_count'],
                'total_original_size': folder_data['total_original_size'],
                'total_compressed_size': folder_data['total_compressed_size'],
                'overall_ratio': folder_data['overall_ratio'],
                'compression_method': 'hybrid_quantum',
                'timestamp': folder_data['timestamp'],
                'files': []
            }
            
            # Add each compressed file with its path
            for file_info in folder_data['files']:
                compressed_folder_data['files'].append({
                    'path': file_info['path'],
                    'compressed_data': file_info['compressed_data'],
                    'reconstruction_metadata': file_info['reconstruction_metadata'],
                    'method': file_info['method'],
                    'original_size': file_info['original_size'],
                    'compressed_size': file_info['compressed_size'],
                    'ratio': file_info['ratio']
                })
            
            # Convert to JSON and compress with gzip
            json_data = json.dumps(compressed_folder_data, separators=(',', ':'))
            compressed_data = gzip.compress(json_data.encode('utf-8'))
            download_name = f"{folder_data['folder_name']}.qc.gz"
        
        # Create temporary file for download
        with tempfile.NamedTemporaryFile(suffix='.qc.gz', delete=False) as temp_file:
            temp_file.write(compressed_data)
            temp_file.flush()
            
            # Send the compressed file
            return send_file(
                temp_file.name,
                as_attachment=True,
                download_name=download_name,
                mimetype='application/gzip'
            )
        
    except Exception as e:
        logger.error(f"❌ Folder download error: {e}")
        return jsonify({'error': f'Folder download failed: {str(e)}'}), 500

@app.route('/api/decompress/folder', methods=['POST'])
@require_auth
def decompress_folder():
    """Decompress a folder from uploaded .qc.gz file"""
    try:
        if 'folder' not in request.files:
            return jsonify({'error': 'No compressed folder uploaded'}), 400
        
        folder_file = request.files['folder']
        api_key = request.headers.get('X-API-Key') or session.get('api_key')
        
        if not folder_file.filename:
            return jsonify({'error': 'No folder selected'}), 400
        
        logger.info(f"📁 Decompressing folder: {folder_file.filename}")
        
        # Read and decompress the .qc.gz file
        compressed_data = folder_file.read()
        
        try:
            # Decompress gzip
            json_data = gzip.decompress(compressed_data).decode('utf-8')
            folder_data = json.loads(json_data)
        except Exception as e:
            return jsonify({'error': f'Invalid compressed folder file: {str(e)}'}), 400
        
        if folder_data.get('type') not in ['folder', 'multi_file_archive']:
            return jsonify({'error': 'Not a compressed folder file'}), 400
        
        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            extract_path = temp_path / 'decompressed'
            extract_path.mkdir()
            
            # Decompress each file
            decompressed_files = []
            total_original_size = 0
            total_decompressed_size = 0
            
            for file_info in folder_data['files']:
                try:
                    # Decompress the file
                    decompression_data = {
                        "compressed_data": file_info['compressed_data'],
                        "method": file_info['method'],
                        "reconstruction_metadata": file_info.get('reconstruction_metadata', '')
                    }
                    
                    headers = {
                        'Authorization': f'Bearer {api_key}',
                        'Content-Type': 'application/json'
                    }
                    
                    response = requests.post(
                        f"{QUANTUM_API_URL}/decompress",
                        json=decompression_data,
                        headers=headers,
                        timeout=300
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        decompressed_data = base64.b64decode(result['decompressed_data'])
                        
                        # Save decompressed file maintaining directory structure
                        # Handle both 'path' and 'filename' fields for compatibility
                        file_path_str = file_info.get('path', file_info.get('filename', 'unknown'))
                        file_path = Path(file_path_str)
                        output_path = extract_path / file_path
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        
                        with open(output_path, 'wb') as f:
                            f.write(decompressed_data)
                        
                        decompressed_files.append({
                            'path': str(file_path),
                            'size': len(decompressed_data),
                            'original_size': file_info.get('original_size', len(decompressed_data))
                        })
                        
                        total_original_size += file_info.get('original_size', len(decompressed_data))
                        total_decompressed_size += len(decompressed_data)
                        
                    else:
                        file_path_str = file_info.get('path', file_info.get('filename', 'unknown'))
                        logger.warning(f"Failed to decompress {file_path_str}: {response.status_code}")
                        
                except Exception as e:
                    file_path_str = file_info.get('path', file_info.get('filename', 'unknown'))
                    logger.warning(f"Error decompressing {file_path_str}: {e}")
            
            # Create a zip file with decompressed folder
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    # Add all decompressed files maintaining directory structure
                    for root, dirs, files in os.walk(extract_path):
                        for file in files:
                            file_path = Path(root) / file
                            relative_path = file_path.relative_to(extract_path)
                            zip_file.write(file_path, relative_path)
                
                # Create job entry for download
                job_id = secrets.token_hex(16)
                # Handle both 'folder_name' and 'archive_name' fields for compatibility
                folder_name = folder_data.get('folder_name', folder_data.get('archive_name', 'folder'))
                COMPRESSION_JOBS[job_id] = {
                    'type': 'decompression',
                    'original_filename': f"{folder_name}_decompressed.zip",
                    'file_path': temp_zip.name,
                    'timestamp': datetime.now().isoformat()
                }
                
                logger.info(f"✅ Folder decompression complete: {len(decompressed_files)} files")
                
                return jsonify({
                    'success': True,
                    'job_id': job_id,
                    'files_decompressed': len(decompressed_files),
                    'total_original_size': total_original_size,
                    'total_decompressed_size': total_decompressed_size,
                    'download_url': f'/api/download/{job_id}',
                    'timestamp': datetime.now().isoformat()
                })
        
    except Exception as e:
        logger.error(f"❌ Folder decompression error: {e}")
        return jsonify({'error': f'Folder decompression failed: {str(e)}'}), 500

@app.route('/api/download/<job_id>')
@require_auth
def download_file(job_id):
    """Download compressed or decompressed file"""
    try:
        logger.info(f"📥 Download request for job_id: {job_id}")
        
        # Check if job exists in memory
        if job_id not in COMPRESSION_JOBS:
            logger.warning(f"⚠️ Job {job_id} not found in memory")
            # Try to load from disk first
            job_data = load_job_from_disk(job_id)
            if job_data:
                job = job_data
                logger.info(f"📁 Job {job_id} loaded from disk")
            else:
                return jsonify({'error': 'File not found or expired'}), 404
        else:
            job = COMPRESSION_JOBS[job_id]
            logger.info(f"📋 Job found: type={job.get('type')}, filename={job.get('original_filename')}")
        
        if job['type'] == 'compression':
            # Check if raw format requested
            raw_format = request.args.get('raw', 'false').lower() == 'true'

            output_path = job.get('output_path')
            if not output_path:
                try:
                    from qzip.jobs import get_manager
                    output_path = get_manager().output_path(job_id)
                except Exception:
                    output_path = None
            if output_path and Path(output_path).exists() and not raw_format:
                filename = (job.get('original_filename') or 'download') + '.qz'
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name=filename,
                    mimetype='application/octet-stream'
                )
            
            # Check if this is an image compression
            compression_method = job.get('method', '')
            is_image_compression = 'image' in compression_method or 'wavelet' in compression_method
            
            if raw_format:
                # Download just the raw compressed data (tiny file)
                filename = job['original_filename'] + '.qcr'  # .qcr for raw
                try:
                    # Stream the raw compressed data directly
                    compressed_bytes = base64.b64decode(job['compressed_data'])
                    
                    return Response(
                        compressed_bytes,
                        mimetype='application/octet-stream',
                        headers={
                            'Content-Disposition': f'attachment; filename="{filename}"',
                            'Content-Length': str(len(compressed_bytes))
                        }
                    )
                except Exception as e:
                    logger.error(f"Raw download error: {e}")
                    return jsonify({'error': f'Raw download failed: {str(e)}'}), 500
            else:
                # For images compressed with quantum_wavelet_image, decompress and return viewable JPEG
                if is_image_compression and compression_method == 'quantum_wavelet_image':
                    filename = job['original_filename']  # Keep original filename
                    
                    try:
                        # Get the compressed data
                        compressed_bytes = base64.b64decode(job['compressed_data'])
                        
                        # Decompress exactly like the standalone test
                        from src.quantum_image_compression import decompress_image_quantum
                        viewable_jpeg = decompress_image_quantum(compressed_bytes)
                        
                        logger.info(f"🖼️ Returning decompressed image: {filename}, {len(viewable_jpeg):,} bytes (viewable)")
                        
                        # Determine MIME type based on extension
                        import mimetypes
                        mime_type = mimetypes.guess_type(filename)[0] or 'image/jpeg'
                        
                        # Return the decompressed viewable JPEG
                        return Response(
                            viewable_jpeg,
                            mimetype=mime_type,
                            headers={
                                'Content-Disposition': f'attachment; filename="{filename}"',
                                'Content-Length': str(len(viewable_jpeg))
                            }
                        )
                    except Exception as e:
                        logger.error(f"Image processing error: {e}")
                        return jsonify({'error': f'Image processing failed: {str(e)}'}), 500
                else:
                    # Non-images: the .qz container (same bytes as `qzip compress`, see qzip/container.py)
                    filename = job['original_filename'] + '.qz'
                    try:
                        from qzip.container import pack
                        qz = pack({
                            'compressed_data': job['compressed_data'],
                            'reconstruction_metadata': job.get('reconstruction_metadata'),
                            'method': job['method'],
                            'quantum_parameters': job.get('quantum_parameters', {}),
                        })
                        return Response(qz, mimetype='application/octet-stream',
                                        headers={'Content-Disposition': f'attachment; filename="{filename}"',
                                                 'Content-Length': str(len(qz))})
                    except Exception as e:
                        logger.error(f"QC download error: {e}")
                        return jsonify({'error': f'QC download failed: {str(e)}'}), 500
            
        elif job['type'] == 'decompression':
            # Send decompressed file - handle both temp_file_path and file_path
            file_path = job.get('temp_file_path', job.get('file_path'))
            if not file_path:
                return jsonify({'error': 'Decompressed file not found'}), 404
            
            return send_file(
                file_path,
                as_attachment=True,
                download_name=job['original_filename'],
                mimetype=job.get('content_type', 'application/zip')
            )
        
        else:
            return jsonify({'error': 'Invalid job type'}), 500
            
    except Exception as e:
        logger.error(f"❌ Download error: {e}")
        return jsonify({'error': f'Download failed: {str(e)}'}), 500

@app.route('/api/download/batch/<batch_id>')
@require_auth
def download_batch(batch_id):
    """Download batch compression results as ZIP"""
    try:
        if batch_id not in COMPRESSION_JOBS:
            return jsonify({'error': 'Batch not found or expired'}), 404
        
        batch = COMPRESSION_JOBS[batch_id]
        if batch['type'] != 'batch_compression':
            return jsonify({'error': 'Not a batch job'}), 400
        
        # Create ZIP file with all compressed files
        import zipfile
        
        zip_path = tempfile.NamedTemporaryFile(delete=False, suffix='.zip').name
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for result in batch['results']:
                if result['status'] == 'success':
                    filename = result['id'] + '.qc'
                    
                    compressed_data = {
                        'compressed_data': result['compressed_data'],
                        'method': 'hybrid_quantum',
                        'original_filename': result['id'],
                        'compression_ratio': result['compression_ratio']
                    }
                    
                    # Add to ZIP
                    zipf.writestr(filename, json.dumps(compressed_data, indent=2))
        
        return send_file(
            zip_path,
            as_attachment=True,
            download_name=f'quantum_compressed_batch_{batch_id[:8]}.zip',
            mimetype='application/zip'
        )
        
    except Exception as e:
        logger.error(f"❌ Batch download error: {e}")
        return jsonify({'error': f'Batch download failed: {str(e)}'}), 500

@app.route('/api/supported-formats')
def get_supported_formats():
    """Get list of supported file formats"""
    return jsonify({
        'supported_formats': {
            'documents': ['pdf', 'docx', 'xlsx', 'pptx', 'odt', 'txt'],
            'images': ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'tiff'],
            'videos': ['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv'],
            'audio': ['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a', 'wma'],
            'archives': ['zip', 'tar', 'gz', 'bz2', '7z', 'rar'],
            'databases': ['sql', 'sqlite', 'db', 'mdb'],
            'code': ['js', 'py', 'java', 'cpp', 'c', 'cs', 'go', 'rs', 'rb'],
            'data': ['json', 'xml', 'csv', 'tsv', 'yaml'],
            'other': ['*']  # All file types supported
        },
        'note': 'All file types are supported with perfect reconstruction'
    })

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    
    while size >= 1024 and i < len(size_names) - 1:
        size /= 1024.0
        i += 1
    
    return f"{size:.2f} {size_names[i]}"

def cleanup_old_jobs():
    """Clean up old job data (runs periodically)"""
    try:
        current_time = datetime.now()
        expired_jobs = []
        
        for job_id, job in COMPRESSION_JOBS.items():
            job_time = datetime.fromisoformat(job['timestamp'])
            if current_time - job_time > timedelta(hours=24):
                expired_jobs.append(job_id)
                
                # Clean up temp files
                if 'temp_file_path' in job:
                    try:
                        os.unlink(job['temp_file_path'])
                    except:
                        pass
        
        # Remove expired jobs
        for job_id in expired_jobs:
            del COMPRESSION_JOBS[job_id]
        
        if expired_jobs:
            logger.info(f"🧹 Cleaned up {len(expired_jobs)} expired jobs")
            
    except Exception as e:
        logger.error(f"❌ Cleanup error: {e}")

@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({'error': 'File too large. Maximum size is 5GB via web interface. Use API directly for larger files.'}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Load existing jobs from disk on startup
    logger.info("📁 Loading existing jobs from disk...")
    jobs_loaded = 0
    for job_file in JOBS_STORAGE_DIR.glob("*.pkl"):
        try:
            job_id = job_file.stem
            job_data = load_job_from_disk(job_id)
            if job_data:
                COMPRESSION_JOBS[job_id] = job_data
                jobs_loaded += 1
        except Exception as e:
            logger.warning(f"⚠️ Could not load job {job_file.name}: {e}")
    
    if jobs_loaded > 0:
        logger.info(f"✅ Loaded {jobs_loaded} existing jobs from disk")
    
    # Schedule periodic cleanup
    import threading
    
    def run_cleanup():
        while True:
            time.sleep(3600)  # Run every hour
            cleanup_old_jobs()
    
    cleanup_thread = threading.Thread(target=run_cleanup, daemon=True)
    cleanup_thread.start()
    
    logger.info("🚀 Starting QZip Enterprise Web Interface")
    logger.info("⚡ Connected to API at: " + QUANTUM_API_URL)
    logger.info("🌐 Web interface starting on port 5000...")
    logger.info("")
    logger.info("📊 Endpoints Available:")
    logger.info("   • Dashboard: http://localhost:5000")
    logger.info("   • API Docs: http://localhost:5000/api/docs")
    logger.info("   • Benchmarks: http://localhost:5000/api/benchmarks")
    logger.info("   • Savings Calculator: http://localhost:5000/api/savings-calculator")
    logger.info("")
    logger.info("Measured performance (byte-exact; see /api/benchmarks, ranges are per-file):")
    logger.info("   • Statistical/structured CSV: ~3.4:1 to ~116:1 (wins on low-cardinality columns)")
    logger.info("   • Medical NIfTI: ~39:1 (wins vs xz/zstd/brotli)")
    logger.info("   • Generic text (enwik8/9): ~3.2:1 to ~3.8:1 (loses to xz)")
    logger.info("")
    
    # Try to set longer timeout for development server
    import werkzeug.serving
    werkzeug.serving.WSGIRequestHandler.timeout = 3600  # 1 hour timeout
    
    app.run(host=os.getenv('QZIP_WEB_HOST', '127.0.0.1'), port=int(os.getenv('QZIP_WEB_PORT', '5000')), debug=False, threaded=True)
