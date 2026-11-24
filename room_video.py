"""Private, seekable video files for the lifetime of a room."""
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from flask import abort, jsonify, render_template, request, send_file, session, url_for
from werkzeug.exceptions import ClientDisconnected


MIB = 1024 * 1024
PREFIX = '/api/room-video/'
FILE_ID = re.compile(r'[0-9a-f]{32}')
SCHEMA = """CREATE TABLE IF NOT EXISTS room_video_files (
    id TEXT PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    owner TEXT NOT NULL, bytes INTEGER NOT NULL, created REAL NOT NULL, mime TEXT
)"""


def configured_mib(key, default):
    value = os.environ.get(key, str(default))
    if not value.isascii() or not value.isdecimal() or len(value) > 7:
        raise ValueError(f'{key} must be a non-negative number of MiB.')
    return int(value) * MIB


def max_upload_bytes():
    return configured_mib('SOLACE_ROOM_VIDEO_MAX_MIB', 128)


def server_body_limit():
    return max(8 * MIB + 16384, max_upload_bytes())


def video_type(header):
    if len(header) >= 16 and header[4:8] == b'ftyp' and int.from_bytes(header[:4], 'big') >= 16:
        return 'video/mp4'
    if header.startswith(b'\x1a\x45\xdf\xa3') and b'\x42\x82\x84webm' in header[:4096]:
        return 'video/webm'
    return None


def valid_room_source(connection, room, owner, source):
    key = source.removeprefix(PREFIX)
    return FILE_ID.fullmatch(key) and connection.execute(
        'SELECT 1 FROM room_video_files WHERE id=? AND room=? AND owner=? AND mime IS NOT NULL',
        (key, room, owner),
    ).fetchone()


def prune_room_videos(connection, instance, now):
    """Run under a write transaction. Readers already holding a file may finish."""
    directory = Path(instance) / 'room-videos'
    if not directory.is_dir():
        return 0
    connection.execute(SCHEMA)
    connection.execute("""
        DELETE FROM room_video_files WHERE created < ? AND (
            mime IS NULL OR (
                NOT EXISTS (SELECT 1 FROM rooms WHERE rooms.id=room_video_files.room AND rooms.video=? || room_video_files.id)
                AND NOT EXISTS (SELECT 1 FROM queue WHERE queue.room=room_video_files.room AND queue.video=? || room_video_files.id)
            )
        )
    """, (now - 86400, PREFIX, PREFIX))
    count = 0
    for path in directory.iterdir():
        if FILE_ID.fullmatch(path.name) and not connection.execute(
            'SELECT 1 FROM room_video_files WHERE id=?', (path.name,)
        ).fetchone():
            try:
                path.unlink(missing_ok=True)
                count += 1
            except PermissionError:
                # Windows can keep a file open while a disconnected reader drains.
                continue
    return count


def register_room_video(app, db):
    def member(room):
        return db().execute('SELECT 1 FROM members WHERE room=? AND id=?', (room, session['uid'])).fetchone()

    def available(key):
        if not FILE_ID.fullmatch(key):
            abort(404)
        row = db().execute(
            """SELECT f.mime FROM room_video_files f JOIN members m ON m.room=f.room
               WHERE f.id=? AND m.id=? AND f.mime IS NOT NULL""",
            (key, session['uid']),
        ).fetchone()
        if not row:
            abort(404)
        return row['mime']

    @app.post('/api/me/room-video/<room>')
    def upload_room_video(room):
        maximum = max_upload_bytes()
        size = request.content_length
        if not member(room):
            abort(403)
        if not maximum:
            return jsonify(error='Local video uploads are disabled on this server.'), 403
        if not size or size > maximum:
            return jsonify(error=f'Choose a non-empty file up to {maximum // MIB} MiB.'), 413
        request.max_content_length = maximum
        limited = app.extensions['solace_launch']['limited']
        if limited('room-video-upload:'+session['uid'], 4, 60) or limited('room-video-upload-ip', 12, 60):
            return jsonify(error='Too many video uploads. Wait a minute and try again.'), 429
        directory = Path(app.instance_path) / 'room-videos'
        directory.mkdir(mode=0o700, exist_ok=True)
        connection = db()
        connection.execute('BEGIN IMMEDIATE')
        prune_room_videos(connection, app.instance_path, time.time())
        if not member(room):
            abort(403)
        reserved, pending, files = connection.execute(
            'SELECT COALESCE(SUM(bytes),0), COALESCE(SUM(CASE WHEN mime IS NULL THEN bytes ELSE 0 END),0), COUNT(*) FROM room_video_files'
        ).fetchone()
        budget = configured_mib('SOLACE_ROOM_VIDEO_STORAGE_MIB', 1024)
        if files >= 2048 or reserved + size > budget or shutil.disk_usage(directory).free < pending + size + 256 * MIB:
            return jsonify(error='This server does not have enough space for that video.'), 507
        key = secrets.token_hex(16)
        connection.execute(
            'INSERT INTO room_video_files VALUES (?,?,?,?,?,NULL)', (key, room, session['uid'], size, time.time())
        )
        connection.commit()
        path = directory / key
        complete = False
        try:
            sample = request.stream.read(min(size, 4096))
            mime = video_type(sample)
            if not mime:
                return jsonify(error='Choose a browser-playable MP4 or WebM file.'), 415
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as target:
                target.write(sample)
                shutil.copyfileobj(request.stream, target, length=MIB)
                if target.tell() != size:
                    return jsonify(error='The upload ended early. Try again.'), 400
            connection.execute('BEGIN IMMEDIATE')
            if not member(room):
                abort(403)
            if not connection.execute('UPDATE room_video_files SET mime=? WHERE id=?', (mime, key)).rowcount:
                return jsonify(error='The upload expired. Try again.'), 409
            connection.commit()
            complete = True
            return jsonify(url=url_for('room_video_content', key=key)), 201
        except ClientDisconnected:
            return jsonify(error='The upload was interrupted. Try again.'), 400
        except OSError:
            return jsonify(error='The server could not store this video.'), 507
        finally:
            if not complete:
                connection.execute('DELETE FROM room_video_files WHERE id=?', (key,))
                connection.commit()
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    app.logger.warning('An incomplete room video is awaiting storage cleanup.')

    @app.get(PREFIX + '<key>')
    def room_video_content(key):
        mime = available(key)
        path = Path(app.instance_path) / 'room-videos' / key
        try:
            response = send_file(path, mimetype=mime, conditional=True, etag=False)
        except FileNotFoundError:
            abort(404)
        response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response

    @app.get('/room-video-player/<key>')
    def room_video_player(key):
        available(key)
        return render_template('room-video-player.html', source=url_for('room_video_content', key=key))

    @app.get('/room-video-upload')
    def room_video_upload():
        room = request.args.get('room', '')
        if not member(room):
            abort(404)
        return render_template('room-video-upload.html', room=room, limit=max_upload_bytes() // MIB)
