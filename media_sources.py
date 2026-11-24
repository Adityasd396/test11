"""Bounded HTTPS media inspection. Never proxy or download complete videos."""
import base64
import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin, parse_qs
from urllib.request import getproxies


class SourceError(ValueError):
    pass


class InspectionUnavailable(SourceError):
    def __init__(self, url, message):
        super().__init__(message)
        self.url = url


# Trusted providers can use hostname CONNECT for proxies with hostname-based policies.
PROXY_HOSTS=('media.w3.org','storage.googleapis.com','pixeldrain.com','www.pixeldrain.com',
             'pixeldrain.dev','gpdl.hubcloud.mov','gpdl.rohitkiskk.workers.dev',
             'cryptonewz.online','.r2.cloudflarestorage.com')


class PinnedProxyConnection(http.client.HTTPConnection):
    """Tunnel to a validated address and authenticate TLS against the origin."""

    def __init__(self, proxy_host, proxy_port, *, origin, timeout):
        super().__init__(proxy_host, proxy_port, timeout=timeout)
        self.origin = origin
        self.context = ssl.create_default_context()

    def connect(self):
        super().connect()
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.origin)


def proxy_connection(host,address,timeout):
    proxies=getproxies()
    value=proxies.get('https') or proxies.get('http')
    if not value:return None
    trusted=os.environ.get('SOLACE_MEDIA_PROXY_HOSTS')
    names=[x.strip().lower() for x in trusted.split(',') if x.strip()] if trusted is not None else (*PROXY_HOSTS,'*')
    trusted_hostname=any(host==name or (name=='.r2.cloudflarestorage.com' and host.endswith(name)) for name in names)
    if not trusted_hostname and '*' not in names:
        raise SourceError('This media provider is not enabled for inspection on this server. Ask the site administrator to enable it.')
    try:
        proxy=urlsplit(value if '://' in value else 'http://'+value)
        if proxy.scheme!='http' or not proxy.hostname or proxy.path not in ('','/') or proxy.query or proxy.fragment:
            raise ValueError()
        port=proxy.port or 80
        if not 1<=port<=65535:raise ValueError()
    except ValueError:
        raise SourceError('The server outbound proxy configuration is invalid. Contact the site administrator.') from None
    headers={}
    if proxy.username is not None:
        from urllib.parse import unquote
        credentials=unquote(proxy.username)+':'+unquote(proxy.password or '')
        headers['Proxy-Authorization']='Basic '+base64.b64encode(credentials.encode()).decode('ascii')
    # Proxy credentials go only in CONNECT, never in the origin GET request.
    if trusted_hostname:
        conn=http.client.HTTPSConnection(proxy.hostname,port,timeout=timeout,context=ssl.create_default_context())
        conn.set_tunnel(host,443,headers=headers)
    else:
        conn=PinnedProxyConnection(proxy.hostname,port,origin=host,timeout=timeout)
        conn.set_tunnel(address,443,headers=headers)
    return conn


def validate_url(value):
    if not isinstance(value,str) or len(value)>8192 or '\\' in value or any(ord(c)<33 or ord(c)==127 for c in value):
        raise SourceError('Enter a valid HTTPS media link.')
    try:
        u=urlsplit(value)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or u.port not in (None,443):
            raise ValueError()
        host=u.hostname.rstrip('.')
        try:
            address=ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host or host.endswith(('.localhost','.local')) or re.fullmatch(r'(?:(?:0x[0-9a-f]+|[0-9]+)\.)*(?:0x[0-9a-f]+|[0-9]+)',host,re.I):
                raise ValueError() from None
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError()
    except ValueError:
        raise SourceError('Use an HTTPS link without credentials, fragments or custom ports.') from None
    return u


def public_addresses(host):
    try:
        addresses=list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)))
    except OSError:
        raise SourceError('The source could not be reached. Check the link and try again.') from None
    if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast or ipaddress.ip_address(ip).is_reserved for ip in addresses):
        raise SourceError('Private or local network sources are not supported.')
    return addresses


def fetch_sample(url, timeout=6):
    u=validate_url(url)
    # Pin the validated IP while preserving TLS SNI, certificate checks and Host.
    addresses=public_addresses(u.hostname)
    try:
        conn=proxy_connection(u.hostname,addresses[0],timeout)
    except SourceError as error:
        raise InspectionUnavailable(url,str(error)) from None
    proxied=conn is not None
    if not proxied:
        conn=http.client.HTTPSConnection(u.hostname,443,timeout=timeout,context=ssl.create_default_context())
        conn._create_connection=lambda address,timeout,source_address: socket.create_connection((addresses[0],443),timeout,source_address)
    try:
        path=u.path or '/'
        if u.query:path+='?'+u.query  # Do not parse or re-encode signed query strings.
        request_headers={'Range':'bytes=0-16383','Accept-Encoding':'identity','User-Agent':'Solace-Media/1.0'}
        if isinstance(conn,PinnedProxyConnection):
            request_headers['Host']=u.netloc.encode('idna').decode('ascii')
        conn.request('GET',path,headers=request_headers)
        response=conn.getresponse()
        headers={k.lower():v for k,v in response.getheaders()}
        limit=65536 if headers.get('content-type','').lower().startswith('text/html') else 16384
        body=b'' if response.status in (301,302,303,307,308) else response.read(limit)
        return response.status,headers,body
    except (OSError,http.client.HTTPException,UnicodeError) as error:
        if proxied:
            denied=re.search(r'Tunnel connection failed: (\d{3})',str(error))
            if denied and denied[1]=='403':
                raise InspectionUnavailable(url,'The hosting outbound proxy denied this media provider (HTTP 403).') from None
            if denied and denied[1]=='407':
                raise InspectionUnavailable(url,'The server outbound proxy requires authentication (HTTP 407).') from None
            raise InspectionUnavailable(url,'The server outbound proxy could not connect to this media provider.') from None
        raise InspectionUnavailable(url,'The server could not read this media source in time.') from None
    finally:
        conn.close()


def media_kind(headers,body):
    mime=headers.get('content-type','').split(';')[0].lower()
    if len(body)>12 and body[4:8]==b'ftyp':return 'mp4'
    if body.startswith(b'\x1aE\xdf\xa3'):return 'matroska' if b'matroska' in body[:4096] else 'webm'
    if body.lstrip().startswith(b'#EXTM3U'):return 'hls'
    if mime.startswith(('video/','audio/')):return mime
    if mime in ('application/vnd.apple.mpegurl','application/x-mpegurl'):return 'hls'
    if b'<MPD' in body[:2048]:raise SourceError('DASH manifests are not supported by this player. Use an MP4, WebM or compatible HLS link.')
    return None


class MediaPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.media = []
        self.downloads = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        src = values.get('src')
        if tag in ('video', 'audio', 'source') and src:
            self.media.append(src)
        if tag == 'meta' and values.get('content') and values.get('property') in ('og:video:url', 'og:video:secure_url', 'og:video'):
            self.media.append(values['content'])
        href = values.get('href')
        if tag == 'a' and href:
            label = ' '.join(values.get(key) or '' for key in ('id', 'class', 'aria-label'))
            if 'download' in values or re.search(r'download|direct|\bvd\b', label, re.I) or re.search(r'\.(?:mp4|m4v|webm|mkv|m3u8)(?:\?|$)', href, re.I):
                self.downloads.append(href)


def page_media(url,body):
    """Resolve explicit media/download anchors only; never execute site scripts."""
    u=validate_url(url)
    if u.hostname in ('pixeldrain.com','pixeldrain.dev','www.pixeldrain.com'):
        match=re.fullmatch(r'/u/([A-Za-z0-9]{8})',u.path)
        if match:return 'https://'+u.hostname+'/api/file/'+match[1]
    text=body.decode('utf-8','replace')
    # This download interstitial assigns its button from one URL query value.
    # Decode only that outer value; do not execute page scripts or alter the inner URL.
    if 'downloadBtn' in text and re.search(r"\.get\(['\"]link['\"]\)",text):
        target=parse_qs(u.query).get('link',[''])[0]
        if target:return target
    page=MediaPage()
    page.feed(text)
    for candidate in page.media+page.downloads:
        target=urljoin(url,candidate)
        try:
            validate_url(target)
        except SourceError:
            continue
        return target
    raise SourceError('This page does not expose a supported direct media link. Copy its final download URL; login, CAPTCHA and script-only downloads cannot be resolved.')


def resolve_media(url,fetch=fetch_sample):
    initial=validate_url(url)
    # Public sharing pages use the provider's documented file API. Do not
    # rewrite direct/signed URLs or carry page-only parameters onto a download.
    if initial.hostname in ('pixeldrain.dev','pixeldrain.com','www.pixeldrain.com') and not initial.query:
        match=re.fullmatch(r'/u/([A-Za-z0-9]{8})',initial.path)
        if match:url='https://pixeldrain.com/api/file/'+match[1]
    seen=set();deadline=time.monotonic()+20
    for _ in range(6):
        if url in seen:raise SourceError('The source returned a redirect loop.')
        if time.monotonic()>deadline:raise SourceError('The source took too long to resolve.')
        seen.add(url)
        # Validate every redirect/page hop, not just the original input.
        validate_url(url)
        status,headers,body=fetch(url,timeout=max(.2,min(6,deadline-time.monotonic()))) if fetch is fetch_sample else fetch(url)
        if status in (401,403,410):raise SourceError('This link has expired or requires permission. Generate a fresh public download link.')
        if status==404:raise SourceError('The media file was not found.')
        if status in (405,416):raise InspectionUnavailable(url,'This source does not accept the server’s media check.')
        if status in (301,302,303,307,308):
            if not headers.get('location'):raise SourceError('The source returned an invalid redirect.')
            url=urljoin(url,headers['location']);continue
        if status not in (200,206):raise SourceError('The source is unavailable (HTTP '+str(status)+').')
        kind=media_kind(headers,body)
        if kind:
            return dict(url=url,format=kind,range_supported=status==206 or headers.get('accept-ranges','').lower()=='bytes',
                        warning='MKV playback and codecs vary by browser. If playback fails, use H.264/AAC MP4 or a compatible WebM source.' if kind=='matroska' else None)
        url=page_media(url,body)
    raise SourceError('This download has too many redirects. Use its final media URL.')
