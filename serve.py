"""Cloud entrypoint. Put an HTTPS reverse proxy in front of this server."""
from ipaddress import ip_address
import os
import signal
os.environ.setdefault('SOLACE_ENV','production')
from waitress import create_server
from app import app
from room_video import server_body_limit

app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', '1') == '1'

if __name__ == '__main__':
    from production import configuration_errors
    errors=configuration_errors(app)
    if errors:raise SystemExit('Launch configuration: '+'; '.join(errors))
    proxy_options = {}
    if os.environ.get('TRUST_PROXY') == '1':
        try:
            proxy_address = str(ip_address(os.environ.get('SOLACE_TRUSTED_PROXY', '127.0.0.1')))
        except ValueError:
            raise SystemExit('SOLACE_TRUSTED_PROXY must be the IP address of the immediate reverse proxy.') from None
        proxy_options = {
            'trusted_proxy': proxy_address,
            'trusted_proxy_headers': {'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto'},
        }
    server=create_server(app,host=os.environ.get('HOST','0.0.0.0'),port=int(os.environ.get('PORT','8080')),threads=8,channel_timeout=30,
        connection_limit=100,backlog=128,max_request_header_size=16384,max_request_body_size=server_body_limit(),outbuf_high_watermark=1048576,expose_tracebacks=False,log_socket_errors=False,**proxy_options)
    def stopping(signum,frame):
        app.extensions['solace_draining']=True
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stopping)
    if hasattr(signal,'SIGBREAK'):signal.signal(signal.SIGBREAK,stopping)
    try:server.run()
    finally:
        app.extensions['solace_draining']=True
        server.task_dispatcher.shutdown(timeout=10);server.close()
