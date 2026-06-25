from flask import Flask, request, jsonify, session, redirect, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
import requests, os, uuid, subprocess, random, time, secrets, threading, json, base64
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__, static_folder='../frontend')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(16))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///../codeforge.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

REPO_CHANGES_DIR = os.path.join(os.path.dirname(__file__), '../.codeforge_changes')
os.makedirs(REPO_CHANGES_DIR, exist_ok=True)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    github_id = db.Column(db.String(100), unique=True)
    username = db.Column(db.String(100))
    name = db.Column(db.String(200))
    avatar_url = db.Column(db.Text)
    github_token = db.Column(db.Text)

class Repository(db.Model):
    __tablename__ = 'repositories'
    id = db.Column(db.Integer, primary_key=True)
    repo_id = db.Column(db.String(100), unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    name = db.Column(db.String(255))
    full_name = db.Column(db.String(500))
    description = db.Column(db.Text)
    is_private = db.Column(db.Boolean, default=False)
    pending_changes = db.Column(db.Boolean, default=False)

class Workspace(db.Model):
    __tablename__ = 'workspaces'
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.String(100), unique=True)
    repo_id = db.Column(db.Integer)
    user_id = db.Column(db.Integer)
    repo_name = db.Column(db.String(255))
    container_name = db.Column(db.String(255))
    port = db.Column(db.Integer)
    vscode_url = db.Column(db.String(500))
    status = db.Column(db.String(50), default='running')
    created_at = db.Column(db.DateTime, default=datetime.now)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Login required'}), 401
        return f(*args, **kwargs)
    return decorated

def get_repo_changes_path(repo_id):
    return os.path.join(REPO_CHANGES_DIR, f'changes_{repo_id}.json')

def load_repo_changes(repo_id):
    changes_path = get_repo_changes_path(repo_id)
    if os.path.exists(changes_path):
        with open(changes_path, 'r') as f:
            return json.load(f)
    return {'files': {}, 'deleted_files': [], 'last_save': None, 'change_count': 0}

def save_repo_changes(repo_id, changes):
    changes['change_count'] = len(changes.get('files', {})) + len(changes.get('deleted_files', []))
    changes['last_save'] = datetime.now().isoformat()
    changes_path = get_repo_changes_path(repo_id)
    with open(changes_path, 'w') as f:
        json.dump(changes, f, indent=2)

@app.route('/auth/github')
def github_login():
    client_id = os.getenv('GITHUB_CLIENT_ID')
    if not client_id:
        return "Error: GITHUB_CLIENT_ID not configured", 500
    return redirect(f'https://github.com/login/oauth/authorize?client_id={client_id}&redirect_uri=http://localhost:5000/auth/github/callback&scope=repo,user')

@app.route('/auth/github/callback')
def github_callback():
    code = request.args.get('code')
    if not code:
        return "Error: No code provided", 400
    
    client_id = os.getenv('GITHUB_CLIENT_ID')
    client_secret = os.getenv('GITHUB_CLIENT_SECRET')
    
    resp = requests.post('https://github.com/login/oauth/access_token',
        json={'client_id': client_id, 'client_secret': client_secret, 'code': code},
        headers={'Accept': 'application/json'})
    
    token_data = resp.json()
    token = token_data.get('access_token')
    
    user_resp = requests.get('https://api.github.com/user', headers={'Authorization': f'token {token}'})
    user_data = user_resp.json()
    
    user = User.query.filter_by(github_id=str(user_data['id'])).first()
    if not user:
        user = User(
            github_id=str(user_data['id']), 
            username=user_data['login'],
            name=user_data.get('name', user_data['login']), 
            avatar_url=user_data['avatar_url'], 
            github_token=token
        )
        db.session.add(user)
        db.session.commit()
    
    repos_resp = requests.get('https://api.github.com/user/repos', headers={'Authorization': f'token {token}'})
    if repos_resp.status_code == 200:
        repos = repos_resp.json()
        for repo in repos:
            if not Repository.query.filter_by(repo_id=str(repo['id'])).first():
                db.session.add(Repository(
                    repo_id=str(repo['id']), 
                    user_id=user.id,
                    name=repo['name'], 
                    full_name=repo['full_name'],
                    description=repo.get('description', ''), 
                    is_private=repo['private']
                ))
        db.session.commit()
    
    session['user_id'] = user.id
    return redirect('/dashboard')

@app.route('/api/user/profile')
@login_required
def profile():
    user = db.session.get(User, session['user_id'])
    return jsonify({'username': user.username, 'name': user.name, 'avatar_url': user.avatar_url})

@app.route('/api/repositories')
@login_required
def repositories():
    repos = Repository.query.filter_by(user_id=session['user_id']).all()
    result = []
    for r in repos:
        changes = load_repo_changes(r.repo_id)
        has_changes = changes.get('change_count', 0) > 0
        if has_changes and not r.pending_changes:
            r.pending_changes = True
            db.session.commit()
        result.append({
            'id': r.id, 'name': r.name, 'description': r.description or '', 
            'is_private': r.is_private, 'full_name': r.full_name,
            'pending_changes': r.pending_changes or has_changes,
            'change_count': changes.get('change_count', 0)
        })
    return jsonify(result)

@app.route('/api/repo/changes/<int:repo_id>', methods=['GET'])
@login_required
def get_repo_changes(repo_id):
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repo not found'}), 404
    changes = load_repo_changes(repo.repo_id)
    return jsonify(changes)

@app.route('/api/repo/changes/<int:repo_id>', methods=['POST'])
@login_required
def save_repo_changes_endpoint(repo_id):
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repo not found'}), 404
    
    data = request.get_json()
    changes = load_repo_changes(repo.repo_id)
    
    if 'files' in data:
        for path, content in data['files'].items():
            changes['files'][path] = content
    if 'deleted_files' in data:
        for path in data['deleted_files']:
            if path in changes['files']:
                del changes['files'][path]
            if path not in changes['deleted_files']:
                changes['deleted_files'].append(path)
    
    save_repo_changes(repo.repo_id, changes)
    
    if changes['change_count'] > 0:
        repo.pending_changes = True
        db.session.commit()
    
    return jsonify({'message': 'Changes saved', 'change_count': changes['change_count']})

@app.route('/api/repo/clear-changes/<int:repo_id>', methods=['POST'])
@login_required
def clear_repo_changes(repo_id):
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repo not found'}), 404
    
    save_repo_changes(repo.repo_id, {'files': {}, 'deleted_files': [], 'last_save': None, 'change_count': 0})
    repo.pending_changes = False
    db.session.commit()
    return jsonify({'message': 'Changes cleared'})

@app.route('/api/repo/commit/<int:repo_id>', methods=['POST'])
@login_required
def commit_to_github(repo_id):
    data = request.get_json()
    commit_message = data.get('commit_message', 'CodeForge: Update files')
    
    repo = db.session.get(Repository, repo_id)
    if not repo or repo.user_id != session['user_id']:
        return jsonify({'error': 'Repo not found'}), 404
    
    user = db.session.get(User, session['user_id'])
    changes = load_repo_changes(repo.repo_id)
    
    if changes['change_count'] == 0:
        return jsonify({'error': 'No changes to commit'}), 400
    
    headers = {'Authorization': f'token {user.github_token}', 'Accept': 'application/vnd.github.v3+json'}
    
    # Get default branch
    repo_info = requests.get(f"https://api.github.com/repos/{repo.full_name}", headers=headers).json()
    default_branch = repo_info.get('default_branch', 'main')
    
    # Get current commit SHA
    ref_resp = requests.get(f"https://api.github.com/repos/{repo.full_name}/git/refs/heads/{default_branch}", headers=headers)
    if ref_resp.status_code != 200:
        return jsonify({'error': 'Could not get branch info'}), 500
    current_sha = ref_resp.json()['object']['sha']
    
    # Prepare tree updates
    tree_updates = []
    for file_path, content in changes['files'].items():
        tree_updates.append({'path': file_path, 'mode': '100644', 'type': 'blob', 'content': content})
    for file_path in changes['deleted_files']:
        tree_updates.append({'path': file_path, 'mode': '100644', 'type': 'blob', 'sha': None})
    
    # Create new tree
    tree_resp = requests.post(f"https://api.github.com/repos/{repo.full_name}/git/trees", 
                              headers=headers, json={'base_tree': current_sha, 'tree': tree_updates})
    if tree_resp.status_code != 201:
        return jsonify({'error': 'Failed to create tree'}), 500
    new_tree_sha = tree_resp.json()['sha']
    
    # Create commit
    commit_resp = requests.post(f"https://api.github.com/repos/{repo.full_name}/git/commits",
                                headers=headers, json={'message': commit_message, 'tree': new_tree_sha, 'parents': [current_sha]})
    if commit_resp.status_code != 201:
        return jsonify({'error': 'Failed to create commit'}), 500
    new_commit_sha = commit_resp.json()['sha']
    
    # Update reference
    update_resp = requests.patch(f"https://api.github.com/repos/{repo.full_name}/git/refs/heads/{default_branch}",
                                 headers=headers, json={'sha': new_commit_sha, 'force': False})
    if update_resp.status_code != 200:
        return jsonify({'error': 'Failed to update reference'}), 500
    
    # Clear changes after successful commit
    save_repo_changes(repo.repo_id, {'files': {}, 'deleted_files': [], 'last_save': None, 'change_count': 0})
    repo.pending_changes = False
    db.session.commit()
    
    return jsonify({'message': 'Successfully committed to GitHub!', 'commit_sha': new_commit_sha})

@app.route('/api/workspace/create', methods=['POST'])
@login_required
def create_workspace():
    data = request.get_json()
    repo = db.session.get(Repository, data['repo_id'])
    if not repo:
        return jsonify({'error': 'Repo not found'}), 404
    
    user = db.session.get(User, session['user_id'])
    changes = load_repo_changes(repo.repo_id)
    
    existing = Workspace.query.filter_by(repo_id=repo.id, user_id=user.id).first()
    if existing:
        check = subprocess.run(['docker', 'ps', '-q', '-f', f'name={existing.container_name}'], capture_output=True)
        if check.returncode == 0 and check.stdout:
            return jsonify({'workspace_id': existing.workspace_id, 'vscode_url': existing.vscode_url, 'reused': True, 'changes': changes})
        else:
            db.session.delete(existing)
            db.session.commit()
    
    port = random.randint(30000, 31000)
    workspace_id = str(uuid.uuid4())[:8]
    container_name = f"codeforge-{repo.name}-{workspace_id}".lower().replace('_', '-')
    clone_url = f"https://{user.github_token}@github.com/{repo.full_name}.git"
    
    changes_json = json.dumps(changes)
    changes_b64 = base64.b64encode(changes_json.encode()).decode()
    
    result = subprocess.run([
        'docker', 'run', '-d', '--name', container_name, '-p', f'{port}:8080',
        '-e', f'REPO_URL={clone_url}', '-e', f'REPO_NAME={repo.name}',
        '-e', f'CODEFORGE_CHANGES={changes_b64}', '-e', f'WORKSPACE_ID={workspace_id}',
        '--restart', 'unless-stopped', 'codeforge-base:latest'
    ], capture_output=True)
    
    if result.returncode != 0:
        return jsonify({'error': f'Failed to create container: {result.stderr.decode()}'}), 500
    
    time.sleep(5)
    
    workspace = Workspace(workspace_id=workspace_id, repo_id=repo.id, user_id=user.id, repo_name=repo.name,
                         container_name=container_name, port=port, vscode_url=f"http://localhost:{port}", status='running')
    db.session.add(workspace)
    db.session.commit()
    
    return jsonify({'workspace_id': workspace_id, 'vscode_url': f"http://localhost:{port}", 'reused': False, 'changes': changes})

@app.route('/api/workspace/sync/<workspace_id>', methods=['POST'])
@login_required
def sync_workspace(workspace_id):
    data = request.get_json()
    workspace = Workspace.query.filter_by(workspace_id=workspace_id, user_id=session['user_id']).first()
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    
    repo = db.session.get(Repository, workspace.repo_id)
    if repo:
        changes = load_repo_changes(repo.repo_id)
        if 'files' in data:
            for path, content in data['files'].items():
                changes['files'][path] = content
        if 'deleted_files' in data:
            for path in data['deleted_files']:
                if path in changes['files']:
                    del changes['files'][path]
                if path not in changes['deleted_files']:
                    changes['deleted_files'].append(path)
        
        save_repo_changes(repo.repo_id, changes)
        
        if changes['change_count'] > 0:
            repo.pending_changes = True
            db.session.commit()
    
    return jsonify({'message': 'Changes synced'})

@app.route('/api/workspace/destroy/<workspace_id>', methods=['POST'])
@login_required
def destroy_workspace(workspace_id):
    workspace = Workspace.query.filter_by(workspace_id=workspace_id, user_id=session['user_id']).first()
    if not workspace:
        return jsonify({'error': 'Workspace not found'}), 404
    
    subprocess.run(['docker', 'rm', '-f', workspace.container_name], capture_output=True)
    db.session.delete(workspace)
    db.session.commit()
    return jsonify({'message': 'Workspace destroyed'})

@app.route('/api/workspace/check/<int:repo_id>')
@login_required
def check_workspace(repo_id):
    workspace = Workspace.query.filter_by(repo_id=repo_id, user_id=session['user_id']).first()
    if workspace:
        check = subprocess.run(['docker', 'ps', '-q', '-f', f'name={workspace.container_name}'], capture_output=True)
        if check.returncode == 0 and check.stdout:
            return jsonify({'exists': True, 'workspace_id': workspace.workspace_id, 'status': 'running'})
        else:
            db.session.delete(workspace)
            db.session.commit()
    return jsonify({'exists': False, 'status': 'none'})

@app.route('/api/auth/logout', methods=['POST'])
@login_required
def logout():
    for workspace in Workspace.query.filter_by(user_id=session['user_id']).all():
        subprocess.run(['docker', 'rm', '-f', workspace.container_name], capture_output=True)
        db.session.delete(workspace)
    db.session.commit()
    session.clear()
    return jsonify({'message': 'Logged out'})

@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect('/')
    return send_from_directory('../frontend', 'dashboard.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    print("\n" + "="*60)
    print("🚀 CodeForge is running at http://localhost:5000")
    print("="*60)
    print("\n✅ ALL FEATURES IMPLEMENTED:")
    print("   • Real-time file tracking with inotify")
    print("   • Before close: Push to GitHub or save locally")
    print("   • Custom commit messages")
    print("   • Pending changes badge with count")
    print("   • Commit button from dashboard")
    print("\n" + "="*60 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
