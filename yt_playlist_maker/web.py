"""The page and its JSON API, on 127.0.0.1 only. Requests must name this app's own host, and changes must be
JSON, so another site open in his browser cannot drive it."""
from __future__ import annotations

import logging
from html import escape

from flask import Flask, abort, jsonify, redirect, request, send_from_directory

from . import google_auth, listenbrainz, lists, net, playlists, store, worker
from .config import ROOT, Config

logger = logging.getLogger(__name__)
STATIC = ROOT / 'static'
HOSTNAME = 'yt-playlist-maker.localhost'


def create(cfg: Config) -> Flask:
    app = Flask(__name__, static_folder=None)
    hosts = {HOSTNAME, f'127.0.0.1:{cfg.port}', f'localhost:{cfg.port}'}
    signing_in: dict[str, str] = {}

    @app.before_request
    def own_host_only():
        if request.host not in hosts:
            abort(403)
        if request.method == 'POST' and not request.is_json:
            abort(415)

    @app.errorhandler(ValueError)
    @app.errorhandler(lists.Failed)
    @app.errorhandler(google_auth.SignInNeeded)
    def refused(exc):
        return jsonify({'error': str(exc)}), 400

    @app.errorhandler(KeyError)
    def missing(exc):
        return jsonify({'error': f'No such item: {exc}'}), 404

    @app.errorhandler(net.ServiceError)
    @app.errorhandler(net.Unreachable)
    @app.errorhandler(net.OverBudget)
    def outside(exc):
        return jsonify({'error': str(exc)}), 502

    @app.get('/')
    def index():
        return send_from_directory(STATIC, 'index.html')

    @app.get('/static/<path:name>')
    def static_files(name):
        return send_from_directory(STATIC, name)

    @app.get('/api/state')
    def state():
        return jsonify({
            'setup': {
                'listenbrainz_token': bool(cfg.listenbrainz_token),
                'youtube_key': bool(cfg.youtube_api_key),
                'google_client': bool(cfg.google_client_id and cfg.google_client_secret),
                'signed_in': google_auth.signed_in(),
                'language_model': cfg.language_model,
            },
            'youtube_left': net.youtube_left(),
            'lists': lists.summaries(),
            'limits': {'lines': lists.MAX_LINES, 'line_chars': lists.MAX_LINE_CHARS, 'songs': list(lists.SONGS)},
        })

    @app.post('/api/lists')
    def new_list():
        body = request.get_json()
        list_id = lists.create(str(body.get('text', '')), int(body.get('songs', 3)))
        return jsonify({'id': list_id}), 201

    @app.get('/api/lists/<int:list_id>')
    def one_list(list_id):
        return jsonify(lists.get(list_id))

    @app.post('/api/lists/<int:list_id>/retry')
    def retry(list_id):
        lists.get(list_id)
        lists.note(list_id, error='', wait_until=None, wait_reason='', tries=0)
        return jsonify({'ok': True})

    @app.post('/api/lists/<int:list_id>/read-again')
    def read_again(list_id):
        lists.get(list_id)
        lists.set_step(list_id, 'reading')
        return jsonify({'ok': True})

    @app.post('/api/picks/<int:pick_id>/swap')
    def swap(pick_id):
        lists.swap(pick_id)
        return jsonify({'ok': True})

    @app.post('/api/picks/<int:pick_id>/remove')
    def remove(pick_id):
        lists.remove(pick_id)
        return jsonify({'ok': True})

    @app.post('/api/lines/<int:line_id>/artist')
    def choose_artist(line_id):
        lists.choose_artist(line_id, str(request.get_json().get('mbid', '')))
        return jsonify({'ok': True})

    @app.post('/api/lists/<int:list_id>/playlist')
    def make_playlist(list_id):
        if not google_auth.signed_in():
            raise google_auth.SignInNeeded('Sign in to YouTube first')
        playlists.start(list_id, str(request.get_json().get('visibility', 'unlisted')))
        return jsonify({'ok': True})

    @app.get('/google/signin')
    def google_signin():
        state, url = google_auth.start(cfg)
        back = request.args.get('back', '/')
        signing_in[state] = f'http://{request.host}{back if back.startswith("/") else "/"}'
        return redirect(url)

    @app.get('/google/callback')
    def google_callback():
        state = request.args.get('state', '')
        back = signing_in.pop(state, f'http://{HOSTNAME}/')
        if request.args.get('error'):
            return _message(f'Google says: {request.args["error"]}. Nothing was changed.', back)
        try:
            google_auth.finish(cfg, state, request.args.get('code', ''))
        except (google_auth.SignInNeeded, net.ServiceError, net.Unreachable) as exc:
            return _message(f'Signing in did not work: {exc}', back)
        return redirect(back)

    @app.post('/api/google/signout')
    def google_signout():
        forget = bool(request.get_json().get('forget'))
        deleted = None
        try:
            google_auth.sign_out()
        finally:
            if forget:
                deleted = worker.forget_youtube()
        return jsonify({'ok': True, 'deleted': deleted})

    return app


def _message(text: str, back: str):
    return (f'<!doctype html><meta charset="utf-8"><title>YouTube sign-in</title>'
            f'<p style="font:18px system-ui;color:#000">{escape(text)}</p>'
            f'<p style="font:18px system-ui"><a href="{escape(back)}">Back to the playlist maker</a></p>'), 400


def main() -> None:
    from . import config
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    cfg = config.load()
    listenbrainz.use_token(cfg.listenbrainz_token)
    store.setup()
    worker.Worker(cfg).start()
    create(cfg).run(host='127.0.0.1', port=cfg.port, threaded=True)
