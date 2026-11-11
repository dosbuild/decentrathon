"""Loopback-only HTTP adapter for the packaged dalel workspace."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import zipfile

from dalel import __version__
from dalel.resources import PACKAGE_ROOT
from dalel.workspace.service import Conflict, WorkspaceService

ASSETS = PACKAGE_ROOT / 'assets/workspace'
MAX_BODY = 64 * 1024 * 1024


class WorkspaceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, service):
        self.service = service
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'DalelWorkspace/' + __version__

    def log_message(self, *args):
        # ARB content and request bodies never enter access logs.
        pass

    def _headers(self, status, mime, length, *, download=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="' + download.replace('"', '') + '"')
        self.end_headers()

    def _json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf8')
        self._headers(status, 'application/json; charset=utf-8', len(raw))
        self.wfile.write(raw)

    def _guard(self, *, token=True):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in allowed:
            raise PermissionError('This server accepts only its localhost address')
        origin = self.headers.get('Origin')
        if origin is not None and origin not in {'http://' + value for value in allowed}:
            raise PermissionError('Cross-origin requests are not accepted')
        if token and not secrets.compare_digest(self.headers.get('X-Dalel-Token', ''), self.server.token):
            raise PermissionError('Reload dalel to refresh the local session')

    def _body(self):
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('JSON request body required')
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > MAX_BODY:
            raise ValueError('Request must be between 1 byte and 64 MiB')
        value = json.loads(self.rfile.read(length), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Non-JSON constant')))
        if not isinstance(value, dict):
            raise ValueError('Request must be an object')
        return value

    def _error(self, exc):
        if isinstance(exc, Conflict):
            self._json(409, {'error': str(exc), 'code': 'stale_workspace'})
        elif isinstance(exc, PermissionError):
            self._json(403, {'error': str(exc), 'code': 'forbidden'})
        elif isinstance(exc, (ValueError, KeyError, TypeError, UnicodeError)):
            self._json(422, {'error': str(exc), 'code': 'invalid_input'})
        else:
            self._json(500, {'error': 'The local backend could not finish this operation. Your saved workspace is preserved.', 'code': 'backend_error'})

    def do_GET(self):
        try:
            path = unquote(urlsplit(self.path).path)
            if path == '/api/bootstrap':
                self._guard(token=False)
                self._json(200, {'token': self.server.token, 'version': __version__, 'simulation': self.server.service.simulation,
                                 'modelCallsAllowed': False, 'analysisProfile': 'offline'})
                return
            if path.startswith('/api/'):
                self._guard()
                if path == '/api/workspaces/current':
                    self._json(200, {'workspace': self.server.service.current()}); return
                if path == '/api/workspaces':
                    self._json(200, {'workspaces': self.server.service.list()}); return
                parts = path.strip('/').split('/')
                if len(parts) == 4 and parts[:2] == ['api', 'workspaces'] and parts[3] == 'review':
                    from dalel.workspace.decisions import export
                    self._json(200, export(self.server.service.get(parts[2]))); return
                if len(parts) == 3 and parts[:2] == ['api', 'workspaces']:
                    self._json(200, {'workspace': self.server.service.get(parts[2])}); return
                if len(parts) >= 6 and parts[:2] == ['api', 'workspaces'] and parts[3] == 'releases':
                    state = self.server.service.get(parts[2])
                    release = next((r for r in state['releases'] if r['id'] == parts[4]), None)
                    if release is None: raise ValueError('Unknown prepared release')
                    if release['contentFingerprint'] != self.server.service.fingerprint(state):
                        raise Conflict('The workspace changed. Prepare updated release files first.')
                    name = '/'.join(parts[5:])
                    if name == 'package.zip':
                        stream = io.BytesIO()
                        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                            for filename, content in release['files'].items():
                                archive.writestr(filename, content.encode('utf8'))
                        raw = stream.getvalue(); mime = 'application/zip'; filename = 'dalel-' + state['targetLocale'] + '.zip'
                    else:
                        if name not in release['files']: raise ValueError('Unknown release file')
                        raw = release['files'][name].encode('utf8'); filename = Path(name).name
                        mime = 'text/html; charset=utf-8' if name.endswith('.html') else 'application/json; charset=utf-8' if name.endswith(('.arb', '.json')) else 'text/plain; charset=utf-8'
                    self._headers(200, mime, len(raw), download=filename); self.wfile.write(raw); return
                self._json(404, {'error': 'Unknown API route'}); return
            self._guard(token=False)
            relative = 'index.html' if path in ('/', '/index.html', '/standalone.html') else path.lstrip('/')
            candidate = (ASSETS / relative).resolve()
            if 'demo' in candidate.parts or candidate.name == 'demo.js' or not candidate.is_relative_to(ASSETS.resolve()) or not candidate.is_file() or candidate.suffix not in ('.html', '.js', '.css', '.arb', '.csv'):
                self._json(404, {'error': 'Not found'}); return
            mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.arb': 'application/json', '.csv': 'text/csv'}[candidate.suffix]
            raw = candidate.read_bytes()
            self._headers(200, mime + '; charset=utf-8', len(raw)); self.wfile.write(raw)
        except Exception as exc:
            self._error(exc)

    def do_POST(self):
        try:
            self._guard()
            body = self._body(); path = unquote(urlsplit(self.path).path)
            service = self.server.service
            if path == '/api/workspaces':
                self._json(201, {'workspace': service.create(body['workspace'])}); return
            if path == '/api/workspaces/clear':
                service.clear(); self._json(200, {'workspace': None}); return
            parts = path.strip('/').split('/')
            if len(parts) != 4 or parts[:2] != ['api', 'workspaces']:
                self._json(404, {'error': 'Unknown API route'}); return
            ident, operation = parts[2:]
            expected = body.get('expectedVersion')
            if operation != 'activate' and type(expected) is not int:
                raise ValueError('An exact expectedVersion is required')
            if operation == 'activate': result = service.activate(ident)
            elif operation == 'save': result = service.save(ident, expected, body['workspace'])
            elif operation == 'analyze': result = service.analyze(ident, expected)
            elif operation == 'review': result = service.decide(ident, expected, body)
            elif operation == 'finding': result = service.manual(ident, expected, body)
            elif operation == 'import-review': result = service.import_review(ident, expected, body['packet'])
            elif operation == 'flutter': result = service.flutter(ident, expected, body.get('mode', 'validated'))
            elif operation == 'prepare': result = service.prepare(ident, expected, body.get('mode', 'validated'))
            elif operation == 'preview':
                self._json(200, {'release': service.release(ident, expected, body.get('mode', 'validated'))}); return
            else:
                self._json(404, {'error': 'Unknown workspace operation'}); return
            self._json(200, {'workspace': result})
        except Exception as exc:
            self._error(exc)


def serve(*, directory=Path('data/private/workspaces'), port=4173, open_browser=True, simulation=False):
    service = WorkspaceService(directory, simulation=simulation)
    server = WorkspaceHTTPServer(('127.0.0.1', port), service)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'dalel {__version__}: {url}\nLocal backend connected. Model calls disabled. Ctrl+C stops the server.', flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description='Run the connected dalel workspace')
    parser.add_argument('--port', type=int, default=4173)
    parser.add_argument('--data-dir', type=Path, default=Path('data/private/workspaces'))
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--simulation', action='store_true', help='Test operator actions never create human review')
    args = parser.parse_args()
    serve(directory=args.data_dir, port=args.port, open_browser=not args.no_browser, simulation=args.simulation)


if __name__ == '__main__':
    main()
