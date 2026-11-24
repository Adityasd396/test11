"""Owner-only provider overrides stored in the private instance directory."""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

EMAIL = r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+'
SMTP_HOSTS = {'smtp.gmail.com', 'smtp.sendgrid.net', 'smtp.mailgun.org',
              'smtp.postmarkapp.com', 'smtp-relay.brevo.com', 'smtp.office365.com'}

# Each field is defined once for validation, rendering and runtime lookup.
PROVIDERS = {
    'youtube': ('YouTube search', (
        ('YOUTUBE_API_KEY', 'YouTube API key', 'password', 100),
    )),
    'email': ('Account email', (
        ('SOLACE_SMTP_HOST', 'SMTP host', 'text', 253),
        ('SOLACE_SMTP_PORT', 'Port', 'text', 5),
        ('SOLACE_SMTP_SSL', 'TLS mode', 'tls', 1),
        ('SOLACE_MAIL_FROM', 'Sender email', 'email', 254),
        ('SOLACE_SMTP_USERNAME', 'SMTP username', 'password', 254),
        ('SOLACE_SMTP_PASSWORD', 'SMTP password', 'password', 2048),
    )),
    'push': ('Web push', (
        ('SOLACE_PUSH_PUBLIC_KEY', 'VAPID public key', 'text', 87),
        ('SOLACE_PUSH_PRIVATE_KEY', 'VAPID private key (URL-safe base64)', 'password', 43),
        ('SOLACE_PUSH_CONTACT', 'VAPID contact (mailto: address)', 'text', 260),
    )),
    'google_credentials': ('Google credentials', (
        ('SOLACE_GOOGLE_SERVICE_ACCOUNT', 'Service-account JSON', 'secret_json', 16384),
    )),
    'search': ('Search Console', (
        ('SOLACE_SEARCH_PROPERTY', 'Verified Search Console property', 'text', 512),
    )),
    'analytics': ('Google Analytics 4', (
        ('SOLACE_GA4_PROPERTY', 'Numeric GA4 property ID', 'text', 20),
    )),
    'backup': ('Database backups', (
        ('SOLACE_BACKUP_DIRECTORY', 'Private backup directory', 'text', 1024),
    )),
}
FIELDS = {item[0]: item for _, fields in PROVIDERS.values() for item in fields}
SECRET_TYPES = {'password', 'secret_json'}
DEFAULTS = {'SOLACE_SMTP_PORT': '587', 'SOLACE_SMTP_SSL': '0'}


def host_config(app, name, default=''):
    return app.config.get(name, os.environ.get(name, default))


def read_overrides(app):
    path = Path(app.instance_path) / 'provider-settings.json'
    try:
        with path.open(encoding='utf-8') as source:
            return json.loads(source.read(65536))
    except FileNotFoundError:
        return {}


def config_value(app, name, default=''):
    if name in FIELDS:
        settings = read_overrides(app)
        if name in settings:
            return settings[name]
    return host_config(app, name, default)


def describe_fields(app):
    saved = read_overrides(app)
    groups = {}
    for key, (title, fields) in PROVIDERS.items():
        output = []
        for name, label, kind, limit in fields:
            value = saved.get(name, host_config(app, name, DEFAULTS.get(name, '')))
            configured = bool(value)
            if name == 'SOLACE_GOOGLE_SERVICE_ACCOUNT':
                configured = configured or bool(host_config(app, 'GOOGLE_APPLICATION_CREDENTIALS'))
            field = dict(name=name, label=label, type=kind, limit=limit, configured=configured)
            if kind not in SECRET_TYPES:
                field['value'] = str(value)
            output.append(field)
        groups[key] = dict(title=title, fields=output, overridden=any(field[0] in saved for field in fields))
    return groups


def validate(app, name, value):
    _, label, kind, maximum = FIELDS[name]
    if type(value) is not str or len(value) > maximum or any(c in value for c in '\x00\r'):
        raise ValueError('Check '+label+'.')
    if kind != 'secret_json' and '\n' in value:
        raise ValueError('Check '+label+'.')
    if kind not in SECRET_TYPES:
        value = value.strip()
    if not value:
        return ''
    invalid = 'Check '+label+'.'
    patterns = {
        'YOUTUBE_API_KEY': r'[A-Za-z0-9_-]{20,100}',
        'SOLACE_SMTP_PORT': r'465|587',
        'SOLACE_SMTP_SSL': r'[01]',
        'SOLACE_MAIL_FROM': EMAIL,
        'SOLACE_PUSH_CONTACT': 'mailto:'+EMAIL,
        'SOLACE_GA4_PROPERTY': r'\d{1,20}',
    }
    if name in patterns and not re.fullmatch(patterns[name],value,re.ASCII):
        raise ValueError(invalid)
    if name == 'SOLACE_SMTP_HOST':
        allowed = SMTP_HOSTS | {host.strip().lower() for host in str(host_config(app,'SOLACE_SMTP_ALLOWED_HOSTS')).split(',')}
        allowed.add(str(host_config(app,'SOLACE_SMTP_HOST')).strip().lower())
        value = value.lower()
        if value not in allowed:
            raise ValueError('Use a supported SMTP host or add your trusted provider to SOLACE_SMTP_ALLOWED_HOSTS.')
    if name in {'SOLACE_PUSH_PUBLIC_KEY','SOLACE_PUSH_PRIVATE_KEY'}:
        try:
            raw = base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)
        except (ValueError,binascii.Error):
            raise ValueError(invalid) from None
        public = name == 'SOLACE_PUSH_PUBLIC_KEY'
        valid = len(raw) == 65 and raw[0] == 4 if public else len(raw) == 32
        if not re.fullmatch(r'[A-Za-z0-9_-]+',value) or not valid:
            raise ValueError(invalid)
    if name == 'SOLACE_GOOGLE_SERVICE_ACCOUNT':
        try:
            credential=json.loads(value)
            if not isinstance(credential,dict) or credential.get('type')!='service_account' or credential.get('token_uri')!='https://oauth2.googleapis.com/token':
                raise ValueError()
            for field in ('client_email','private_key'):
                if not isinstance(credential.get(field),str) or not credential[field]:
                    raise ValueError()
            if not re.fullmatch(EMAIL,credential['client_email']) or not credential['private_key'].startswith('-----BEGIN PRIVATE KEY-----'):
                raise ValueError()
            # Copy only fields used by the Google signer. Ignore custom endpoint metadata.
            value=json.dumps({field:credential[field] for field in ('type','token_uri','client_email','private_key','private_key_id','project_id') if field in credential})
        except (ValueError,TypeError):
            raise ValueError('Use a Google service-account JSON key from Google Cloud.') from None
    if name == 'SOLACE_SEARCH_PROPERTY':
        if not re.fullmatch(r'sc-domain:[A-Za-z0-9.-]+',value):
            try:
                url=urlsplit(value)
                if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError()
            except ValueError:
                raise ValueError('Enter a verified HTTPS URL prefix or sc-domain property.') from None
    if name == 'SOLACE_BACKUP_DIRECTORY':
        directory=Path(value)
        if not directory.is_absolute():
            raise ValueError('Enter an absolute private backup directory.')
        directory=directory.resolve()
        instance=Path(app.instance_path).resolve()
        if not directory.is_relative_to(Path.home().resolve()) and not directory.is_relative_to(instance):
            raise ValueError('Choose a backup directory within the hosting account home or instance directory.')
        if directory.is_relative_to(Path(app.static_folder).resolve()):
            raise ValueError('Choose a private backup directory outside the public assets.')
        value=str(directory)
    return value


def write_overrides(app, settings):
    path = Path(app.instance_path) / 'provider-settings.json'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.provider-',delete=False) as output:
            temporary=Path(output.name)
            json.dump(settings,output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
