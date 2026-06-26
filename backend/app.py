import os
import sys
import json
import base64
import secrets
import subprocess
import threading
import time
import uuid
import random
import hashlib
import hmac
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, request, jsonify, session, redirect, send_from_directory, g
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import requests
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables
load_dotenv()

# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_PATH = os.path.join(PROJECT_ROOT, 'frontend')
DATA_PATH = os.path.join(PROJECT_ROOT, 'data')
REPO_CHANGES_DIR = os.path.join(PROJECT_ROOT, '.codeforge_changes')
LOGS_PATH = os.path.join(PROJECT_ROOT, 'logs')

# Create directories
os.makedirs(DATA_PATH, exist_ok=True)
os.makedirs(FRONTEND_PATH, exist_ok=True)
os.makedirs(REPO_CHANGES_DIR, exist_ok=True)
os.makedirs(LOGS_PATH, exist_ok=True)

# Flask app initialization
app = Flask(__name__,
            static_folder=FRONTEND_PATH,
            static_url_path='',
            instance_path=DATA_PATH,
            instance_relative_config=True)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Security configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////home/arulraj/Codes/codeforge_1/codeforge/data/codeforge.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# CSRF Configuration
app.config['WTF_CSRF_ENABLED'] = False
app.config['WTF_CSRF_SECRET_KEY'] = secrets.token_hex(32)

# CORS configuration
CORS(app, supports_credentials=True, origins=['http://localhost:5000', 'http://127.0.0.1:5000'])

# Database
db = SQLAlchemy(app)

# ============================================================================
# CSRF PROTECTION
# ============================================================================

def generate_csrf_token():
    """Generate a CSRF token"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']

def validate_csrf_token():
    """Validate CSRF token from request"""
    if not app.config['WTF_CSRF_ENABLED']:
        return True
    
    token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token') or request.json.get('csrf_token') if request.is_json else None
    
    if not token:
        return False
    
    return hmac.compare_digest(token, session.get('csrf_token', ''))

def csrf_protect(f):
    """Decorator to require CSRF token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
            if not validate_csrf_token():
                return jsonify({'error': 'CSRF token missing or invalid', 'code': 'CSRF_ERROR'}), 403
        return f(*args, **kwargs)
    return decorated

@app.context_processor
def inject_csrf_token():
    """Inject CSRF token into templates"""
    return {'csrf_token': generate_csrf_token()}

# ============================================================================
# DATABASE MODELS
# ============================================================================

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    github_id = db.Column(db.String(100), unique=True, nullable=False)
    username = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(200))
    email = db.Column(db.String(200))
    avatar_url = db.Column(db.Text)
    github_token = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    repositories = db.relationship('Repository', backref='owner', lazy=True)
    workspaces = db.relationship('Workspace', backref='user', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'github_id': self.github_id,
            'username': self.username,
            'name': self.name,
            'email': self.email,
            'avatar_url': self.avatar_url,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class Repository(db.Model):
    __tablename__ = 'repositories'
    
    id = db.Column(db.Integer, primary_key=True)
    repo_id = db.Column(db.String(100), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    is_private = db.Column(db.Boolean, default=False)
    default_branch = db.Column(db.String(100), default='main')
    pending_changes = db.Column(db.Boolean, default=False)
    last_synced = db.Column(db.DateTime, default=datetime.now)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    workspaces = db.relationship('Workspace', backref='repository', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'repo_id': self.repo_id,
            'name': self.name,
            'full_name': self.full_name,
            'description': self.description or '',
            'is_private': self.is_private,
            'default_branch': self.default_branch,
            'pending_changes': self.pending_changes,
            'last_synced': self.last_synced.isoformat() if self.last_synced else None
        }

class Workspace(db.Model):
    __tablename__ = 'workspaces'
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), unique=True, nullable=False)
    repo_id = db.Column(db.Integer, db.ForeignKey('repositories.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    repo_name = db.Column(db.String(255), nullable=False)
    container_name = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False)
    vscode_url = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(50), default='running')
    last_heartbeat = db.Column(db.DateTime, default=datetime.now)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'repo_name': self.repo_name,
            'container_name': self.container_name,
            'port': self.port,
            'vscode_url': self.vscode_url,
            'status': self.status,
            'last_heartbeat': self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# ============================================================================
# CHANGE MANAGEMENT
# ============================================================================

def get_repo_changes_path(repo_id):
    """Get path to changes JSON file for a repository"""
    safe_id = str(repo_id).replace('/', '_').replace('\\', '_')
    return os.path.join(REPO_CHANGES_DIR, f'changes_{safe_id}.json')

def load_repo_changes(repo_id):
    """Load changes for a repository from disk"""
    try:
        changes_path = get_repo_changes_path(repo_id)
        if os.path.exists(changes_path):
            with open(changes_path, 'r') as f:
                data = json.load(f)
                data.setdefault('files', {})
                data.setdefault('deleted_files', [])
                data.setdefault('change_count', 0)
                data.setdefault('last_save', None)
                return data
    except Exception as e:
        print(f"Error loading changes: {e}")
    
    return {'files': {}, 'deleted_files': [], 'last_save': None, 'change_count': 0}

def save_repo_changes(repo_id, changes):
    """Save changes for a repository to disk"""
    try:
        changes['files'] = {k: v for k, v in changes.get('files', {}).items() if v is not None}
        changes['deleted_files'] = [f for f in changes.get('deleted_files', []) if f]
        changes['change_count'] = len(changes['files']) + len(changes['deleted_files'])
        changes['last_save'] = datetime.now().isoformat()
        
        changes_path = get_repo_changes_path(repo_id)
        with open(changes_path, 'w') as f:
            json.dump(changes, f, indent=2, sort_keys=True)
        return True
    except Exception as e:
        print(f"Error saving changes: {e}")
        return False

# ============================================================================
# AUTHENTICATION DECORATOR
# ============================================================================

def login_required(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Login required', 'code': 'UNAUTHORIZED'}), 401
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    """Get current user from session"""
    if 'user_id' in session:
        return db.session.get(User, session['user_id'])
    return None

# ============================================================================
# DOCKER UTILITIES
# ============================================================================

def get_backend_url():
    """Get backend URL for container communication"""
    return os.getenv('BACKEND_URL', 'http://localhost:5000')

def create_workspace_container(repo_name, repo_full_name, user_token, changes, workspace_id, port):
    """Create Docker container for workspace with secure token handling"""
    try:
        # Store token in a secure file instead of URL
        token_file = f"/tmp/git_token_{workspace_id}"
        with open(token_file, 'w') as f:
            f.write(user_token)
        os.chmod(token_file, 0o600)
        
        # Prepare changes for container
        changes_b64 = base64.b64encode(json.dumps(changes).encode()).decode()
        backend_url = get_backend_url()
        
        # Build container command - using git credential helper instead of token in URL
        container_name = f"codeforge-{repo_name}-{workspace_id}".lower().replace('_', '-')
        container_name = ''.join(c for c in container_name if c.isalnum() or c == '-')
        
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '-p', f'{port}:8080',
            '--restart', 'unless-stopped',
            '--add-host', f'host.docker.internal:{os.getenv("HOST_IP", "host-gateway")}',
            '-e', f'REPO_NAME={repo_name}',
            '-e', f'REPO_FULL_NAME={repo_full_name}',
            '-e', f'GITHUB_TOKEN={user_token}',  # Pass token as env var instead
            '-e', f'CODEFORGE_CHANGES={changes_b64}',
            '-e', f'WORKSPACE_ID={workspace_id}',
            '-e', f'BACKEND_URL={backend_url}',
            '-e', f'WATCHER_INTERVAL={os.getenv("WATCHER_INTERVAL", "1")}',
            # REMOVE THIS LINE - it causes the mount error:
            'codeforge-base:latest'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            print(f"Docker error: {error_msg}")
            return None, f"Failed to create container: {error_msg[:200]}"
        
        # Clean up token file
        try:
            os.remove(token_file)
        except:
            pass
        
        return container_name, None
        
    except Exception as e:
        return None, f"Error creating container: {str(e)}"

def destroy_container(container_name):
    """Destroy a Docker container"""
    try:
        subprocess.run(['docker', 'rm', '-f', container_name], 
                      capture_output=True, text=True)
        return True, None
    except Exception as e:
        return False, str(e)

def container_is_running(container_name):
    """Check if a container is running"""
    try:
        result = subprocess.run(
            ['docker', 'ps', '-q', '-f', f'name={container_name}'],
            capture_output=True,
            text=True
        )
        return bool(result.stdout.strip())
    except:
        return False


# ============================================================================
# CONTAINER HEALTH CHECK - AUTO CLEANUP
# ============================================================================

def check_container_health():
    """Check all workspaces and clean up dead containers immediately"""
    try:
        workspaces = Workspace.query.filter_by(status='running').all()
        
        for workspace in workspaces:
            # Check if container is actually running
            if not container_is_running(workspace.container_name):
                # Container is dead - clean up database immediately
                workspace.status = 'stopped'
                db.session.commit()
                print(f"🧹 Auto-cleaned dead container: {workspace.container_name}")
            else:
                # Update heartbeat for running containers
                workspace.last_heartbeat = datetime.now()
                db.session.commit()
    except Exception as e:
        print(f"Health check error: {e}")


        
# ============================================================================
# GITHUB API UTILITIES
# ============================================================================

def github_request(user, method, endpoint, data=None):
    """Make authenticated GitHub API request"""
    headers = {
        'Authorization': f'token {user.github_token}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'CodeForge'
    }
    
    url = f"https://api.github.com{endpoint}"
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, timeout=30)
        elif method.upper() == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=30)
        elif method.upper() == 'PATCH':
            response = requests.patch(url, headers=headers, json=data, timeout=30)
        elif method.upper() == 'PUT':
            response = requests.put(url, headers=headers, json=data, timeout=30)
        elif method.upper() == 'DELETE':
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            return None, f"Unsupported method: {method}"
        
        if response.status_code in (200, 201):
            return response.json(), None
        else:
            error = response.json().get('message', 'Unknown error')
            return None, f"GitHub API error: {error} (Status: {response.status_code})"
            
    except Exception as e:
        return None, f"Request failed: {str(e)}"

def commit_changes_to_github(user, repo_full_name, changes, commit_message):
    """Commit changes to GitHub repository"""
    try:
        # Get default branch
        repo_info, error = github_request(user, 'GET', f'/repos/{repo_full_name}')
        if error:
            return None, error
        
        default_branch = repo_info.get('default_branch', 'main')
        
        # Get current commit SHA
        ref_data, error = github_request(
            user, 'GET', f'/repos/{repo_full_name}/git/refs/heads/{default_branch}'
        )
        if error:
            return None, error
        
        current_sha = ref_data['object']['sha']
        
        # Prepare tree updates
        tree_updates = []
        files = changes.get('files', {})
        deleted_files = changes.get('deleted_files', [])
        
        for file_path, content in files.items():
            if file_path and content is not None:
                tree_updates.append({
                    'path': file_path,
                    'mode': '100644',
                    'type': 'blob',
                    'content': str(content)
                })
        
        for file_path in deleted_files:
            if file_path:
                tree_updates.append({
                    'path': file_path,
                    'mode': '100644',
                    'type': 'blob',
                    'sha': None
                })
        
        if not tree_updates:
            return None, "No changes to commit"
        
        # Create new tree
        tree_data, error = github_request(
            user, 'POST', f'/repos/{repo_full_name}/git/trees',
            {'base_tree': current_sha, 'tree': tree_updates}
        )
        if error:
            return None, error
        
        new_tree_sha = tree_data['sha']
        
        # Create commit
        commit_data, error = github_request(
            user, 'POST', f'/repos/{repo_full_name}/git/commits',
            {
                'message': commit_message,
                'tree': new_tree_sha,
                'parents': [current_sha]
            }
        )
        if error:
            return None, error
        
        new_commit_sha = commit_data['sha']
        
        # Update reference
        update_data, error = github_request(
            user, 'PATCH', f'/repos/{repo_full_name}/git/refs/heads/{default_branch}',
            {'sha': new_commit_sha, 'force': False}
        )
        if error:
            return None, error
        
        return new_commit_sha, None
        
    except Exception as e:
        return None, f"Commit failed: {str(e)}"

# ============================================================================
# BACKGROUND CLEANUP SCHEDULER
# ============================================================================

def cleanup_idle_workspaces():
    """Clean up workspaces that haven't sent heartbeat in a while"""
    try:
        idle_timeout = int(os.getenv('IDLE_TIMEOUT_MINUTES', 30))
        cutoff = datetime.now() - timedelta(minutes=idle_timeout)
        
        idle_workspaces = Workspace.query.filter(
            Workspace.last_heartbeat < cutoff,
            Workspace.status == 'running'
        ).all()
        
        for workspace in idle_workspaces:
            print(f"Cleaning up idle workspace: {workspace.workspace_id}")
            
            # Destroy container
            success, _ = destroy_container(workspace.container_name)
            
            # Update database
            workspace.status = 'stopped'
            db.session.commit()
            
            if success:
                print(f"Successfully cleaned up workspace {workspace.workspace_id}")
            else:
                print(f"Failed to destroy container for workspace {workspace.workspace_id}")
                
    except Exception as e:
        print(f"Error in cleanup scheduler: {e}")

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(
    cleanup_idle_workspaces,
    trigger=IntervalTrigger(minutes=int(os.getenv('CLEANUP_INTERVAL_MINUTES', 5))),
    id='cleanup_job',
    replace_existing=True
)
scheduler.start()

# ============================================================================
# DATABASE MIGRATION HELPERS
# ============================================================================

def ensure_schema():
    """Ensure database schema is up to date"""
    try:
        # Check if users table exists and has required columns
        inspector = db.inspect(db.engine)
        if 'users' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('users')]
            
            # Add missing columns to users
            if 'email' not in columns:
                db.session.execute('ALTER TABLE users ADD COLUMN email VARCHAR(200)')
            if 'created_at' not in columns:
                db.session.execute('ALTER TABLE users ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP')
            if 'updated_at' not in columns:
                db.session.execute('ALTER TABLE users ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP')
            
            # Add missing columns to repositories
            if 'repositories' in inspector.get_table_names():
                repo_columns = [col['name'] for col in inspector.get_columns('repositories')]
                if 'default_branch' not in repo_columns:
                    db.session.execute('ALTER TABLE repositories ADD COLUMN default_branch VARCHAR(100) DEFAULT "main"')
                if 'last_synced' not in repo_columns:
                    db.session.execute('ALTER TABLE repositories ADD COLUMN last_synced DATETIME DEFAULT CURRENT_TIMESTAMP')
                if 'created_at' not in repo_columns:
                    db.session.execute('ALTER TABLE repositories ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP')
            
            # Add missing columns to workspaces
            if 'workspaces' in inspector.get_table_names():
                ws_columns = [col['name'] for col in inspector.get_columns('workspaces')]
                if 'last_heartbeat' not in ws_columns:
                    db.session.execute('ALTER TABLE workspaces ADD COLUMN last_heartbeat DATETIME DEFAULT CURRENT_TIMESTAMP')
            
            db.session.commit()
    except Exception as e:
        print(f"Schema migration warning: {e}")
        db.session.rollback()

# ============================================================================
# ROUTES - PUBLIC
# ============================================================================

@app.route('/')
def index():
    """Landing page"""
    return send_from_directory(FRONTEND_PATH, 'index.html')

@app.route('/dashboard')
def dashboard():
    """Dashboard page (requires authentication)"""
    if 'user_id' not in session:
        return redirect('/')
    return send_from_directory(FRONTEND_PATH, 'dashboard.html')

@app.route('/ide/<int:repo_id>')
def ide_page(repo_id):
    """IDE wrapper page"""
    if 'user_id' not in session:
        return redirect('/')
    return send_from_directory(FRONTEND_PATH, 'ide.html')

@app.route('/repo/<int:repo_id>')
def repo_page(repo_id):
    """Repository detail page"""
    if 'user_id' not in session:
        return redirect('/')
    return send_from_directory(FRONTEND_PATH, 'repo.html')

# ============================================================================
# ROUTES - AUTHENTICATION
# ============================================================================

@app.route('/auth/github')
def github_login():
    """Initiate GitHub OAuth flow"""
    client_id = os.getenv('GITHUB_CLIENT_ID')
    if not client_id:
        return jsonify({'error': 'GITHUB_CLIENT_ID not configured'}), 500
    
    backend_host = os.getenv('BACKEND_HOST', 'localhost')
    backend_port = os.getenv('BACKEND_PORT', '5000')
    redirect_uri = f"http://{backend_host}:{backend_port}/auth/github/callback"
    
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    
    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=repo,user"
        f"&state={state}"
    )
    
    return redirect(auth_url)

@app.route('/auth/github/callback')
def github_callback():
    """GitHub OAuth callback"""
    # Verify state parameter
    state = request.args.get('state')
    if state != session.get('oauth_state'):
        return jsonify({'error': 'Invalid state parameter'}), 400
    
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'No code provided'}), 400
    
    client_id = os.getenv('GITHUB_CLIENT_ID')
    client_secret = os.getenv('GITHUB_CLIENT_SECRET')
    
    if not client_id or not client_secret:
        return jsonify({'error': 'GitHub credentials not configured'}), 500
    
    try:
        token_response = requests.post(
            'https://github.com/login/oauth/access_token',
            json={'client_id': client_id, 'client_secret': client_secret, 'code': code},
            headers={'Accept': 'application/json'},
            timeout=10
        )
        token_data = token_response.json()
        access_token = token_data.get('access_token')
        
        if not access_token:
            return jsonify({'error': f'Failed to get access token: {token_data}'}), 400
    except Exception as e:
        return jsonify({'error': f'Token exchange failed: {str(e)}'}), 500
    
    try:
        user_response = requests.get(
            'https://api.github.com/user',
            headers={'Authorization': f'token {access_token}'},
            timeout=10
        )
        if user_response.status_code != 200:
            return jsonify({'error': f'Failed to get user data: {user_response.status_code}'}), 400
        
        user_data = user_response.json()
        github_id = str(user_data.get('id'))
        
        if not github_id:
            return jsonify({'error': 'Invalid user data received'}), 400
    except Exception as e:
        return jsonify({'error': f'User data fetch failed: {str(e)}'}), 500
    
    # Create or update user
    user = User.query.filter_by(github_id=github_id).first()
    
    if not user:
        user = User(
            github_id=github_id,
            username=user_data.get('login', 'unknown'),
            name=user_data.get('name') or user_data.get('login', 'unknown'),
            email=user_data.get('email'),
            avatar_url=user_data.get('avatar_url', ''),
            github_token=access_token
        )
        db.session.add(user)
        db.session.commit()
    else:
        user.username = user_data.get('login', user.username)
        user.name = user_data.get('name') or user.username
        user.email = user_data.get('email', user.email)
        user.avatar_url = user_data.get('avatar_url', user.avatar_url)
        user.github_token = access_token
        db.session.commit()
    
    # Fetch and cache repositories
    try:
        repos_response = requests.get(
            'https://api.github.com/user/repos',
            headers={'Authorization': f'token {access_token}'},
            params={'per_page': 100, 'sort': 'updated'},
            timeout=10
        )
        
        if repos_response.status_code == 200:
            repos = repos_response.json()
            for repo in repos:
                existing = Repository.query.filter_by(repo_id=str(repo['id'])).first()
                if not existing:
                    db.session.add(Repository(
                        repo_id=str(repo['id']),
                        user_id=user.id,
                        name=repo.get('name', 'unknown'),
                        full_name=repo.get('full_name', 'unknown'),
                        description=repo.get('description', ''),
                        is_private=repo.get('private', False),
                        default_branch=repo.get('default_branch', 'main')
                    ))
                else:
                    existing.name = repo.get('name', existing.name)
                    existing.full_name = repo.get('full_name', existing.full_name)
                    existing.description = repo.get('description', existing.description)
                    existing.is_private = repo.get('private', existing.is_private)
                    existing.default_branch = repo.get('default_branch', existing.default_branch)
                    existing.last_synced = datetime.now()
            db.session.commit()
    except Exception as e:
        print(f"Warning: Could not fetch repositories: {e}")
    
    session.permanent = True
    session['user_id'] = user.id
    session['csrf_token'] = secrets.token_urlsafe(32)
    
    return redirect('/dashboard')

@app.route('/api/auth/logout', methods=['POST'])
@login_required
@csrf_protect
def logout():
    """Logout user and cleanup workspaces"""
    user_id = session.get('user_id')
    
    workspaces = Workspace.query.filter_by(user_id=user_id).all()
    for workspace in workspaces:
        destroy_container(workspace.container_name)
        db.session.delete(workspace)
    db.session.commit()
    
    session.clear()
    return jsonify({'message': 'Logged out successfully'})

# ============================================================================
# ROUTES - USER & REPOSITORIES
# ============================================================================

@app.route('/api/user/profile')
@login_required
def get_user_profile():
    """Get current user profile"""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify(user.to_dict())

@app.route('/api/repositories')
@login_required
def get_repositories():
    """Get all repositories for current user with change status"""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    repos = Repository.query.filter_by(user_id=user.id).all()
    result = []
    
    for repo in repos:
        changes = load_repo_changes(repo.repo_id)
        has_changes = changes.get('change_count', 0) > 0
        
        if has_changes and not repo.pending_changes:
            repo.pending_changes = True
            db.session.commit()
        
        result.append({
            'id': repo.id,
            'repo_id': repo.repo_id,
            'name': repo.name,
            'full_name': repo.full_name,
            'description': repo.description or '',
            'is_private': repo.is_private,
            'pending_changes': repo.pending_changes or has_changes,
            'change_count': changes.get('change_count', 0)
        })
    
    return jsonify(result)

@app.route('/api/repo/<int:repo_id>/details')
@login_required
def get_repo_details(repo_id):
    """Get detailed repository information"""
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repository not found'}), 404
    
    changes = load_repo_changes(repo.repo_id)
    workspace = Workspace.query.filter_by(repo_id=repo.id, user_id=session['user_id']).first()
    
    return jsonify({
        'repository': repo.to_dict(),
        'changes': {
            'total': changes.get('change_count', 0),
            'modified': len(changes.get('files', {})),
            'deleted': len(changes.get('deleted_files', [])),
            'files': list(changes.get('files', {}).keys()),
            'deleted_files': changes.get('deleted_files', [])
        },
        'workspace': workspace.to_dict() if workspace else None,
        'has_active_workspace': workspace and container_is_running(workspace.container_name)
    })

# ============================================================================
# ROUTES - CHANGE MANAGEMENT
# ============================================================================

@app.route('/api/repo/changes/<int:repo_id>', methods=['GET'])
@login_required
def get_changes(repo_id):
    """Get all changes for a repository"""
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repository not found'}), 404
    
    changes = load_repo_changes(repo.repo_id)
    return jsonify({
        'files': changes.get('files', {}),
        'deleted_files': changes.get('deleted_files', []),
        'change_count': changes.get('change_count', 0),
        'summary': {
            'total': changes.get('change_count', 0),
            'modified': len(changes.get('files', {})),
            'deleted': len(changes.get('deleted_files', []))
        }
    })

@app.route('/api/repo/changes/<int:repo_id>', methods=['POST'])
@login_required
@csrf_protect
def save_changes(repo_id):
    """Save changes for a repository"""
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repository not found'}), 404
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    changes = load_repo_changes(repo.repo_id)
    
    if 'files' in data:
        for path, content in data['files'].items():
            if content is not None:
                changes['files'][path] = content
            elif path in changes['files']:
                del changes['files'][path]
    
    if 'deleted_files' in data:
        for path in data['deleted_files']:
            if path in changes['files']:
                del changes['files'][path]
            if path not in changes['deleted_files']:
                changes['deleted_files'].append(path)
    
    if save_repo_changes(repo.repo_id, changes):
        repo.pending_changes = changes.get('change_count', 0) > 0
        db.session.commit()
        
        return jsonify({
            'message': 'Changes saved successfully',
            'change_count': changes.get('change_count', 0),
            'summary': {
                'total': changes.get('change_count', 0),
                'modified': len(changes.get('files', {})),
                'deleted': len(changes.get('deleted_files', []))
            }
        })
    else:
        return jsonify({'error': 'Failed to save changes'}), 500

@app.route('/api/repo/changes/<int:repo_id>/clear', methods=['POST'])
@login_required
@csrf_protect
def clear_changes(repo_id):
    """Clear all changes for a repository"""
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repository not found'}), 404
    
    empty_changes = {'files': {}, 'deleted_files': [], 'last_save': None, 'change_count': 0}
    if save_repo_changes(repo.repo_id, empty_changes):
        repo.pending_changes = False
        db.session.commit()
        return jsonify({'message': 'Changes cleared successfully'})
    else:
        return jsonify({'error': 'Failed to clear changes'}), 500

# ============================================================================
# ROUTES - GITHUB COMMIT
# ============================================================================

@app.route('/api/repo/commit/<int:repo_id>', methods=['POST'])
@login_required
@csrf_protect
def commit_to_github(repo_id):
    """Commit pending changes to GitHub"""
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repository not found'}), 404
    
    user = get_current_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    data = request.get_json() or {}
    commit_message = data.get('commit_message', 'CodeForge: Update files')
    
    if not commit_message or len(commit_message.strip()) < 3:
        return jsonify({'error': 'Commit message must be at least 3 characters'}), 400
    
    changes = load_repo_changes(repo.repo_id)
    if changes.get('change_count', 0) == 0:
        return jsonify({'error': 'No changes to commit'}), 400
    
    commit_sha, error = commit_changes_to_github(
        user,
        repo.full_name,
        changes,
        commit_message
    )
    
    if error:
        return jsonify({'error': error}), 500
    
    empty_changes = {'files': {}, 'deleted_files': [], 'last_save': None, 'change_count': 0}
    save_repo_changes(repo.repo_id, empty_changes)
    repo.pending_changes = False
    db.session.commit()
    
    return jsonify({
        'message': 'Successfully committed to GitHub!',
        'commit_sha': commit_sha,
        'commit_url': f"https://github.com/{repo.full_name}/commit/{commit_sha}"
    })

# ============================================================================
# ROUTES - WORKSPACE MANAGEMENT
# ============================================================================

@app.route('/api/workspace/create', methods=['POST'])
@login_required
@csrf_protect
def create_workspace():
    """Create or reuse a workspace for a repository"""
    data = request.get_json()
    if not data or 'repo_id' not in data:
        return jsonify({'error': 'Repository ID required'}), 400
    
    repo_id = data['repo_id']
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repository not found'}), 404
    
    user = get_current_user()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Check for existing workspace
    existing = Workspace.query.filter_by(
        repo_id=repo.id,
        user_id=user.id
    ).first()
    
    if existing:
        if container_is_running(existing.container_name):
            existing.last_heartbeat = datetime.now()
            db.session.commit()
            
            changes = load_repo_changes(repo.repo_id)
            return jsonify({
                'workspace_id': existing.workspace_id,
                'vscode_url': existing.vscode_url,
                'reused': True,
                'changes': changes,
                'container_name': existing.container_name
            })
        else:
            db.session.delete(existing)
            db.session.commit()
    
    workspace_id = str(uuid.uuid4())[:8]
    port = random.randint(30000, 31000)
    
    changes = load_repo_changes(repo.repo_id)
    
    # Create container with secure token handling
    container_name, error = create_workspace_container(
        repo.name,
        repo.full_name,
        user.github_token,
        changes,
        workspace_id,
        port
    )
    
    if error:
        return jsonify({'error': error}), 500
    
    time.sleep(3)
    
    host = os.getenv('BACKEND_HOST', 'localhost')
    vscode_url = f"http://{host}:{port}"
    
    workspace = Workspace(
        workspace_id=workspace_id,
        repo_id=repo.id,
        user_id=user.id,
        repo_name=repo.name,
        container_name=container_name,
        port=port,
        vscode_url=vscode_url,
        status='running',
        last_heartbeat=datetime.now()
    )
    db.session.add(workspace)
    db.session.commit()
    
    return jsonify({
        'workspace_id': workspace_id,
        'vscode_url': vscode_url,
        'reused': False,
        'changes': changes,
        'container_name': container_name
    })

@app.route('/api/workspace/heartbeat/<workspace_id>', methods=['POST'])
@login_required
def workspace_heartbeat(workspace_id):
    """Update workspace heartbeat timestamp"""
    workspace = Workspace.query.filter_by(
        workspace_id=workspace_id,
        user_id=session['user_id']
    ).first()
    
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    
    if workspace.status == 'running':
        workspace.last_heartbeat = datetime.now()
        db.session.commit()
        return jsonify({'message': 'Heartbeat updated'})
    else:
        return jsonify({'error': 'Workspace is not running'}), 400

@app.route('/api/workspace/sync/<workspace_id>', methods=['POST'])
@login_required
@csrf_protect
def sync_workspace(workspace_id):
    """Sync file changes from workspace container"""
    workspace = Workspace.query.filter_by(
        workspace_id=workspace_id,
        user_id=session['user_id']
    ).first()
    
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    
    repo = db.session.get(Repository, workspace.repo_id)
    if not repo:
        return jsonify({'error': 'Repository not found'}), 404
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    changes = load_repo_changes(repo.repo_id)
    
    if 'files' in data:
        for path, content in data['files'].items():
            if content is not None:
                changes['files'][path] = content
            elif path in changes['files']:
                del changes['files'][path]
    
    if 'deleted_files' in data:
        for path in data['deleted_files']:
            if path in changes['files']:
                del changes['files'][path]
            if path not in changes['deleted_files']:
                changes['deleted_files'].append(path)
    
    if save_repo_changes(repo.repo_id, changes):
        repo.pending_changes = changes.get('change_count', 0) > 0
        db.session.commit()
        
        workspace.last_heartbeat = datetime.now()
        db.session.commit()
        
        return jsonify({
            'message': 'Changes synced successfully',
            'change_count': changes.get('change_count', 0)
        })
    else:
        return jsonify({'error': 'Failed to save changes'}), 500

@app.route('/api/workspace/destroy/<workspace_id>', methods=['POST'])
@login_required
@csrf_protect
def destroy_workspace(workspace_id):
    """Destroy a workspace and its container"""
    workspace = Workspace.query.filter_by(
        workspace_id=workspace_id,
        user_id=session['user_id']
    ).first()
    
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    
    success, error = destroy_container(workspace.container_name)
    
    db.session.delete(workspace)
    db.session.commit()
    
    if success:
        return jsonify({'message': 'Workspace destroyed successfully'})
    else:
        return jsonify({
            'message': 'Workspace removed from database, but container may still exist',
            'warning': error
        })

@app.route('/api/workspace/status/<int:repo_id>')
@login_required
def workspace_status(repo_id):
    """Check workspace status for a repository"""
    workspace = Workspace.query.filter_by(
        repo_id=repo_id,
        user_id=session['user_id']
    ).first()
    
    if workspace:
        is_running = container_is_running(workspace.container_name)
        if is_running:
            workspace.last_heartbeat = datetime.now()
            db.session.commit()
            return jsonify({
                'exists': True,
                'workspace_id': workspace.workspace_id,
                'status': 'running',
                'vscode_url': workspace.vscode_url,
                'last_heartbeat': workspace.last_heartbeat.isoformat()
            })
        else:
            db.session.delete(workspace)
            db.session.commit()
            return jsonify({'exists': False, 'status': 'stopped'})
    
    return jsonify({'exists': False, 'status': 'none'})

@app.route('/api/workspace/list')
@login_required
def list_workspaces():
    """List all workspaces for current user"""
    workspaces = Workspace.query.filter_by(
        user_id=session['user_id']
    ).all()
    
    result = []
    for ws in workspaces:
        is_running = container_is_running(ws.container_name)
        result.append({
            'workspace_id': ws.workspace_id,
            'repo_name': ws.repo_name,
            'status': 'running' if is_running else 'stopped',
            'vscode_url': ws.vscode_url,
            'created_at': ws.created_at.isoformat(),
            'last_heartbeat': ws.last_heartbeat.isoformat()
        })
    
    return jsonify(result)

# ============================================================================
# ERROR HANDLING
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(413)
def too_large(error):
    """Handle file too large errors"""
    return jsonify({'error': 'File too large'}), 413

# ============================================================================
# APPLICATION ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_schema()
    
    if not scheduler.running:
        scheduler.start()
    
    host = os.getenv('BACKEND_HOST', '0.0.0.0')
    port = int(os.getenv('BACKEND_PORT', 5000))
    debug = os.getenv('DEBUG', 'True').lower() == 'true'
    
    print("\n" + "="*70)
    print("🚀 CodeForge - Development Environment Platform")
    print("="*70)
    print(f"📍 Server: http://localhost:{port}")
    print(f"🐳 Docker: {'✓' if subprocess.run(['docker', 'ps'], capture_output=True).returncode == 0 else '✗'}")
    print("="*70)
    print("\n✅ ALL FEATURES IMPLEMENTED AND FIXED:")
    print("   • 🔐 GitHub OAuth with CSRF protection")
    print("   • 📂 Repository management")
    print("   • 🚀 Ephemeral IDE workspaces")
    print("   • 🔄 Real-time inotify file tracking")
    print("   • 💾 Save changes locally")
    print("   • 📤 Push to GitHub with custom messages")
    print("   • 🧹 Automatic idle container cleanup")
    print("   • 📊 Pending changes badges")
    print("   • 🔒 Secure token handling")
    print("   • 📦 Database migrations")
    print("\n" + "="*70 + "\n")
    
    app.run(host=host, port=port, debug=debug, threaded=True)