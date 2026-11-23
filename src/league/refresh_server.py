"""Loopback-only dashboard and explicit, read-only Sleeper refresh.

No generic file server, subprocess endpoint, CORS, or Sleeper mutations.
Refresh is staged and validated before publishing; errors retain the old desk.
"""
from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import secrets
import shutil
import threading
import time
import uuid

from src.league.storage import league_dir, load_league, fetch_bundle, import_bundle
from src.league.service import snapshot
from src.reporting.league_dashboard import render_league_dashboard

APP_ID = 'nba-fantasy-league-desk'
STARTED_AT = time.time()  # code and config are loaded once per process


def atomic_bytes(path, content):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_bytes(content)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def refresh_league(cfg, fetch=fetch_bundle, make_snapshot=snapshot):
    root = league_dir(cfg)
    lock = root / 'refresh.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError('Another refresh is running (or was interrupted). Check the local service before retrying.') from exc
    os.close(fd)
    try:
        before, _ = load_league(cfg)
        pointer = root / 'current.json'
        events = root / 'sleeper_events.jsonl'
        old_pointer, old_events = pointer.read_bytes(), events.read_bytes()
        dashboard = cfg.paths['outputs'] / 'league_dashboard.html'
        old_dashboard = dashboard.read_bytes() if dashboard.exists() else None
        with TemporaryDirectory(prefix='sleeper-refresh-') as temp_dir:
            stage = Path(temp_dir)
            stage_cfg = replace(cfg, paths={**cfg.paths, 'repo': stage})
            (stage / 'config').mkdir()
            shutil.copy2(cfg.paths['repo'] / 'config/league_instance.yaml', stage / 'config/league_instance.yaml')
            stage_root = league_dir(stage_cfg)
            stage_root.mkdir(parents=True)
            # Only the current observation and event prefix are needed to replay.
            observation = Path(json.loads(old_pointer)['observation'])
            if not (root / observation).resolve().is_relative_to(root.resolve()):
                raise ValueError('Invalid current observation path')
            for relative in [Path('current.json'), Path('sleeper_events.jsonl'), observation, observation.with_suffix('.manifest.json')]:
                target = stage_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / relative, target)
            # Saved matchup snapshots feed the Lock-In tab; without them a
            # roster refresh would publish a dashboard with no pairings.
            for matchup_pointer in root.glob('matchups-week-*.json'):
                archived = Path(json.loads(matchup_pointer.read_text(encoding='utf-8'))['path'])
                if not (root / archived).resolve().is_relative_to(root.resolve()):
                    raise ValueError('Invalid matchup observation path')
                shutil.copy2(matchup_pointer, stage_root / matchup_pointer.name)
                (stage_root / archived).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / archived, stage_root / archived)
            bundle = fetch(stage_cfg)  # auto-detect current transaction leg
            after, catalog = import_bundle(stage_cfg, bundle)
            data = make_snapshot(stage_cfg)
            if not after.verified:
                raise ValueError('Sleeper ownership did not reconcile')
            changes = []
            for tid, team in after.teams.items():
                old, new = set(before.roster(tid)), set(after.roster(tid))
                reserve_changed = before.reserve.get(tid, set()) != after.reserve.get(tid, set())
                if old != new or reserve_changed:
                    changes.append({'manager': team.manager,
                        'added': [catalog.players[p].name for p in sorted(new - old)],
                        'dropped': [catalog.players[p].name for p in sorted(old - new)],
                        'reserve_changed': reserve_changed})
            data['last_refresh'] = {'observed_at': after.observed_at, 'changes': changes}
            staged_html = stage / 'league_dashboard.html'
            render_league_dashboard(data, staged_html)
            # Existing CLI writers use this event lock; a refresh also prevents
            # new imports while its staging work is in progress.
            event_lock = events.with_suffix('.jsonl.lock')
            event_fd = os.open(event_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(event_fd)
            try:
                if pointer.read_bytes() != old_pointer or events.read_bytes() != old_events:
                    raise ValueError('League changed during refresh; retry to avoid overwriting newer state')
                # Immutable UUID-named archives can be copied before publication.
                for source in (stage_root / 'raw').iterdir():
                    target = root / 'raw' / source.name
                    if not target.exists():
                        shutil.copy2(source, target)
                try:
                    atomic_bytes(events, (stage_root / 'sleeper_events.jsonl').read_bytes())
                    atomic_bytes(pointer, (stage_root / 'current.json').read_bytes())
                    atomic_bytes(dashboard, staged_html.read_bytes())
                except Exception:
                    atomic_bytes(events, old_events)
                    atomic_bytes(pointer, old_pointer)
                    if old_dashboard is not None:
                        atomic_bytes(dashboard, old_dashboard)
                    raise
            finally:
                event_lock.unlink(missing_ok=True)
            return {**data['last_refresh'], 'events_added': after.last_sequence - before.last_sequence,
                'issues': {t['manager']: t['issues'] for t in data['teams'] if t['issues']},
                'forecast_generated_at': data['forecast']['generated_at']}
    finally:
        lock.unlink(missing_ok=True)


class RefreshController:
    def __init__(self, cfg, refresh=refresh_league, cooldown=30, performance=None):
        self.cfg, self.refresh, self.cooldown = cfg, refresh, cooldown
        from src.league.performance_refresh import refresh_performance
        self.performance = performance or refresh_performance
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.last_started = float('-inf')
        self.state = {'status': 'idle'}

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self, kind='sleeper'):
        if kind not in ('sleeper', 'performance'):
            return 400, {'error':'Unknown refresh kind'}
        with self.lock:
            if self.state['status'] == 'running':
                return 202, dict(self.state)
            if time.monotonic() - self.last_started < self.cooldown:
                return 429, {'error': 'Please wait 30 seconds between refreshes.'}
            self.last_started = time.monotonic()
            self.state = {'status': 'running', 'job_id': uuid.uuid4().hex, 'kind':kind}
            result = dict(self.state)
            threading.Thread(target=self._work, args=(kind,), daemon=True).start()
            return 202, result

    def _work(self, kind):
        try:
            result = (self.performance if kind == 'performance' else self.refresh)(self.cfg)
            update = {'status': 'succeeded', 'result': result}
        except Exception as exc:
            update = {'status': 'failed', 'error': str(exc)}
        with self.lock:
            self.state.update(update)


def make_server(cfg, port=8765, controller=None):
    if not 0 <= port <= 65535:
        raise ValueError('Invalid local port')
    controller = controller or RefreshController(cfg)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type='application/json'):
            encoded = json.dumps(body, ensure_ascii=False).encode('utf-8') if content_type == 'application/json' else body
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(encoded)

        def allowed_host(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            return self.headers.get('Host') == expected

        def do_GET(self):
            if not self.allowed_host() or (self.path.startswith('/api/') and self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                self.reply(403, {'error': 'Only the local dashboard can access this service.'})
                return
            if self.path == '/api/session':
                # The launcher restarts a service that predates the latest code/config edit.
                self.reply(200, {'app': APP_ID, 'token': controller.token,
                                 'repo': str(cfg.paths['repo']), 'started_at': STARTED_AT})
            elif self.path == '/api/status':
                self.reply(200, controller.status())
            elif self.path in ('/', '/league_dashboard.html'):
                path = cfg.paths['outputs'] / 'league_dashboard.html'
                if not path.exists():
                    self.reply(503, {'error': 'Build the league dashboard first.'})
                    return
                # Only same-origin service requests are allowed. The disk copy
                # stays network-disabled and never gains access to arbitrary files.
                page = path.read_bytes().replace(b"connect-src 'none'", b"connect-src 'self'")
                self.reply(200, page, 'text/html')
            else:
                self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if (not self.allowed_host() or self.headers.get('Origin') != origin or
                not secrets.compare_digest(self.headers.get('X-Refresh-Token', ''), controller.token)):
                self.reply(403, {'error': 'Refresh authorization failed. Reload the local dashboard.'})
                return
            if self.path not in ('/api/refresh', '/api/refresh-performance'):
                self.reply(404, {'error': 'Not found'})
                return
            if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Content-Length') != '2':
                self.reply(400, {'error': 'Expected an empty JSON object.'})
                return
            if self.rfile.read(2) != b'{}':
                self.reply(400, {'error': 'No refresh parameters are accepted.'})
                return
            status, result = controller.start('performance' if self.path.endswith('-performance') else 'sleeper')
            self.reply(status, result)

    class Server(ThreadingHTTPServer):
        # On Windows SO_REUSEADDR lets a second service bind the same port
        # while the older process keeps receiving every request.
        allow_reuse_address = os.name != 'nt'

    server = Server(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def serve(cfg, port=8765):
    with make_server(cfg, port) as server:
        print(f'League dashboard: http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return {'status': 'stopped'}
