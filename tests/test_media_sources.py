import socket,os
import unittest
from unittest.mock import patch
from media_sources import PinnedProxyConnection, resolve_media,validate_url,public_addresses,SourceError,fetch_sample


class MediaSourcesTests(unittest.TestCase):
 def test_signed_extensionless_url_is_byte_for_byte_unchanged(self):
  url='https://files.example/object?token=a%2Fb%2Bc&file=x%20y&part=1&part=2'
  calls=[]
  def sample(value):
   calls.append(value)
   return 206,{'content-type':'application/octet-stream'},b'\x00\x00\x00\x18ftypisom'+b'0'*20
  result=resolve_media(url,sample)
  self.assertEqual(calls,[url]);self.assertEqual(result['url'],url);self.assertEqual(result['format'],'mp4');self.assertTrue(result['range_supported'])
 def test_redirect_keeps_encoding_and_validates_each_hop(self):
  first='https://files.example/start';final='https://cdn.example/object?signature=a%2Fb&x=1'
  def sample(url):
   return (302,{'location':final},b'') if url==first else (200,{'content-type':'video/webm'},b'webm')
  self.assertEqual(resolve_media(first,sample)['url'],final)
  with self.assertRaises(SourceError):resolve_media(first,lambda _: (302,{'location':'http://127.0.0.1/secret'},b''))
 def test_pixeldrain_and_download_pages(self):
  seen=[]
  def sample(url):
   seen.append(url)
   return (200,{'content-type':'video/mp4'},b'media') if '/api/file/' in url else (200,{'content-type':'text/html'},b'<html>download page</html>')
  self.assertEqual(resolve_media('https://pixeldrain.dev/u/dy1CqWHV',sample)['url'],'https://pixeldrain.com/api/file/dy1CqWHV')
  self.assertEqual(seen,['https://pixeldrain.com/api/file/dy1CqWHV'])
  def hub(url):
   return (200,{'content-type':'video/mp4'},b'media') if '/media?' in url else (200,{'content-type':'text/html'},b'<a download href="/media?token=x%2Fy&amp;k=1">Get file</a>')
  self.assertEqual(resolve_media('https://gpdl.hubcloud.mov/?id=example',hub)['url'],'https://gpdl.hubcloud.mov/media?token=x%2Fy&k=1')
 def test_query_download_button_decodes_only_the_outer_url(self):
  outer='https://page.example/?link=https%3A%2F%2Fcdn.example%2Fraw%3Fsig%3Da%252Fb%26x%3D1'
  def sample(url):
   return (200,{},b'<script>document.getElementById("downloadBtn").href=params.get("link");</script>') if url==outer else (206,{'content-type':'video/mp4'},b'media')
  self.assertEqual(resolve_media(outer,sample)['url'],'https://cdn.example/raw?sig=a%2Fb&x=1')
 def test_generic_download_page_accepts_extensionless_href_before_download_attribute(self):
  page='https://ordinary-files.example/shared'
  source='https://ordinary-files.example/object?sig=a%2Fb&x=1'
  def sample(url):
   if url==page:return 200,{'content-type':'text/html'},b'<a href="/object?sig=a%2Fb&amp;x=1" download>Fetch</a>'
   self.assertEqual(url,source)
   return 206,{'content-type':'video/mp4'},b'media'
  self.assertEqual(resolve_media(page,sample)['url'],source)
 def test_errors_are_actionable_and_loops_bounded(self):
  for code,word in [(403,'expired'),(404,'not found'),(500,'unavailable')]:
   with self.assertRaisesRegex(SourceError,word):resolve_media('https://files.example/object',lambda _: (code,{},b''))
  with self.assertRaisesRegex(SourceError,'loop'):resolve_media('https://files.example/object',lambda _: (302,{'location':'/object'},b''))
  with self.assertRaisesRegex(SourceError,'direct media'):resolve_media('https://files.example/page',lambda _: (200,{},b'<html>Sign in</html>'))
  with self.assertRaisesRegex(SourceError,'DASH'):resolve_media('https://files.example/object',lambda _: (200,{},b'<MPD></MPD>'))
 def test_private_addresses_and_mixed_dns_are_blocked(self):
  for address in ('127.0.0.1','169.254.169.254','10.0.0.1','224.0.0.1','::1','::ffff:127.0.0.1'):
   records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]
   with patch('media_sources.socket.getaddrinfo',return_value=records):
    with self.assertRaises(SourceError):public_addresses('source.example')
  with patch('media_sources.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))]):
   with self.assertRaises(SourceError):public_addresses('source.example')
 def test_invalid_urls_and_browser_format_warning(self):
  for url in ('http://example.com/x','https://a:b@example.com/x','https://example.com:8443/x','https://example.com/x\r\nHeader:yes','https://example.com/x#secret'):
   with self.assertRaises(SourceError):validate_url(url)
  result=resolve_media('https://files.example/object',lambda _: (206,{},b'\x1aE\xdf\xa3matroska'))
  self.assertIn('MKV',result['warning'])
 def test_probe_pins_ip_preserves_query_and_bounds_reads(self):
  with patch('media_sources.getproxies',return_value={}),patch('media_sources.public_addresses',return_value=['8.8.8.8']),patch('media_sources.http.client.HTTPSConnection') as connection:
   response=connection.return_value.getresponse.return_value
   response.status=206;response.getheaders.return_value=[];response.read.return_value=b'ftyp'
   fetch_sample('https://media.example/raw?signature=a%2Fb&x=%20')
   self.assertEqual(connection.return_value.request.call_args.args[1],'/raw?signature=a%2Fb&x=%20')
   response.read.assert_called_once_with(16384);connection.return_value.close.assert_called_once()

 def test_proxy_connect_keeps_signed_query_and_verifies_origin_tls(self):
  with patch.dict(os.environ,{'SOLACE_MEDIA_PROXY_HOSTS':'media.example'}),patch('media_sources.getproxies',return_value={'https':'http://proxy.server:3128'}),patch('media_sources.public_addresses',return_value=['8.8.8.8']),patch('media_sources.ssl.create_default_context') as context,patch('media_sources.http.client.HTTPSConnection') as connection:
   response=connection.return_value.getresponse.return_value
   response.status=206;response.getheaders.return_value=[('Content-Type','video/mp4')];response.read.return_value=b'media'
   status,_,_=fetch_sample('https://media.example/object?signature=a%2Fb&x=%20&x=1')
   self.assertEqual(status,206)
   connection.assert_called_once_with('proxy.server',3128,timeout=6,context=context.return_value)
   connection.return_value.set_tunnel.assert_called_once_with('media.example',443,headers={})
   self.assertEqual(connection.return_value.request.call_args.args[1],'/object?signature=a%2Fb&x=%20&x=1')
   self.assertNotIn('_create_connection',connection.return_value.__dict__)
   connection.return_value.close.assert_called_once()
 def test_proxy_credentials_never_go_to_origin(self):
  with patch.dict(os.environ,{'SOLACE_MEDIA_PROXY_HOSTS':'media.example'}),patch('media_sources.getproxies',return_value={'http':'http://qa:synthetic%2Bpassword@proxy.server:3128'}),patch('media_sources.public_addresses',return_value=['8.8.8.8']),patch('media_sources.http.client.HTTPSConnection') as connection:
   response=connection.return_value.getresponse.return_value;response.status=200;response.getheaders.return_value=[];response.read.return_value=b'media'
   fetch_sample('https://media.example/object')
   self.assertTrue(connection.return_value.set_tunnel.call_args.kwargs['headers']['Proxy-Authorization'].startswith('Basic '))
   self.assertNotIn('Proxy-Authorization',connection.return_value.request.call_args.kwargs['headers'])
 def test_wildcard_pins_public_ip_and_authenticates_original_host(self):
  from unittest.mock import Mock
  with patch.dict(os.environ,{'SOLACE_MEDIA_PROXY_HOSTS':'*'}),patch('media_sources.getproxies',return_value={'https':'http://proxy.server:3128'}),patch('media_sources.public_addresses',return_value=['8.8.8.8']),patch('media_sources.ssl.create_default_context') as tls,patch('media_sources.http.client.HTTPConnection.connect',autospec=True) as tunnel,patch.object(PinnedProxyConnection,'request',autospec=True) as request,patch.object(PinnedProxyConnection,'getresponse') as getresponse:
   sock=Mock()
   tunnel.side_effect=lambda conn:setattr(conn,'sock',sock)
   request.side_effect=lambda conn,*args,**kwargs:conn.connect()
   response=getresponse.return_value
   response.status=206
   response.getheaders.return_value=[('Content-Type','video/mp4')]
   response.read.return_value=b'media'
   fetch_sample('https://unlisted.example/object?signature=a%2Fb&x=%20&x=1')
   connection=request.call_args.args[0]
   self.assertEqual((connection.host,connection.port),('proxy.server',3128))
   self.assertEqual((connection._tunnel_host,connection._tunnel_port),('8.8.8.8',443))
   tls.return_value.wrap_socket.assert_called_once_with(sock,server_hostname='unlisted.example')
   self.assertEqual(request.call_args.args[2],'/object?signature=a%2Fb&x=%20&x=1')
   self.assertEqual(request.call_args.kwargs['headers']['Host'],'unlisted.example')
 def test_wildcard_still_rejects_nonpublic_dns_before_proxy_connect(self):
  with patch.dict(os.environ,{'SOLACE_MEDIA_PROXY_HOSTS':'*'}),patch('media_sources.getproxies',return_value={'https':'http://proxy.server:3128'}),patch('media_sources.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),patch.object(PinnedProxyConnection,'connect') as connect:
   with self.assertRaisesRegex(SourceError,'Private'):fetch_sample('https://unlisted.example/object')
   connect.assert_not_called()
 def test_proxy_denial_is_distinct_and_never_falls_back_to_direct(self):
  with patch.dict(os.environ,{'SOLACE_MEDIA_PROXY_HOSTS':'media.example'}),patch('media_sources.getproxies',return_value={'https':'http://proxy.server:3128'}),patch('media_sources.public_addresses',return_value=['8.8.8.8']),patch('media_sources.http.client.HTTPSConnection') as connection,patch('media_sources.socket.create_connection') as direct:
   connection.return_value.request.side_effect=OSError('Tunnel connection failed: 403 Forbidden')
   with self.assertRaisesRegex(SourceError,'hosting outbound proxy denied'):fetch_sample('https://media.example/object?secret=not-logged')
   self.assertEqual(connection.call_count,1);direct.assert_not_called();connection.return_value.close.assert_called_once()
   connection.return_value.request.side_effect=OSError('Tunnel connection failed: 407 Proxy Authentication Required')
   with self.assertRaisesRegex(SourceError,'requires authentication'):fetch_sample('https://media.example/object')
   direct.assert_not_called()
 def test_proxy_unknown_host_and_private_target_are_rejected_before_connect(self):
  with patch.dict(os.environ,{'SOLACE_MEDIA_PROXY_HOSTS':'media.example'}),patch('media_sources.getproxies',return_value={'https':'http://proxy.server:3128'}),patch('media_sources.http.client.HTTPSConnection') as connection:
   with patch('media_sources.public_addresses',return_value=['8.8.8.8']):
    with self.assertRaisesRegex(SourceError,'not enabled'):fetch_sample('https://attacker.example/object')
   with patch('media_sources.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
    with self.assertRaisesRegex(SourceError,'Private'):fetch_sample('https://media.example/object')
   connection.assert_not_called()
 def test_bad_proxy_configuration_is_not_a_direct_fallback(self):
  for proxy in ('socks5://proxy.server:3128','http://proxy.server:bad','http://proxy.server:3128/path'):
   with self.subTest(proxy=proxy),patch('media_sources.getproxies',return_value={'https':proxy}),patch('media_sources.public_addresses',return_value=['8.8.8.8']),patch('media_sources.http.client.HTTPSConnection') as connection:
    with self.assertRaisesRegex(SourceError,'proxy configuration is invalid'):fetch_sample('https://media.w3.org/object')
    connection.assert_not_called()


if __name__=='__main__':unittest.main()
