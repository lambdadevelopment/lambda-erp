"""Generated, owner-scoped PDF snapshots, shared by chat, MCP and REST.

The download serves the exact validated bytes; subsequent document edits do
not silently replace an attachment. Expired snapshots can be regenerated.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import quote
import uuid

from fastapi import HTTPException
from lambda_erp.database import get_db
from api.pdf import render_document_pdf
from api.pdf_contract import PDFError
from api.pdf_profiles import pdf_metadata
from api.services import get_document_class


MAX_PDF_BYTES = 20 * 1024 * 1024
PDF_RETENTION_DAYS = 7


def pdf_content_disposition(name):
    filename = str(name).replace('/', '_').replace('\\', '_').replace('\r', '').replace('\n', '') + '.pdf'
    return "inline; filename*=UTF-8''" + quote(filename, safe='')


def create_pdf_export(doctype_slug, name, user):
    owner = (user or {}).get('user_id') or (user or {}).get('name')
    if not owner:
        raise HTTPException(401, 'An authenticated PDF owner is required')
    rendered = render_document_pdf(doctype_slug, name)
    data = rendered.data
    if len(data) > MAX_PDF_BYTES:
        raise PDFError('Generated PDF exceeds the 20 MB file limit', code='pdf_too_large')
    doctype, _ = get_document_class(doctype_slug)
    profile = pdf_metadata(doctype)
    stamp = datetime.now(timezone.utc)
    identifier = uuid.uuid4().hex
    digest = sha256(data).hexdigest()
    filename = str(name).replace('/', '_').replace('\\', '_').replace('\r', '').replace('\n', '') + '.pdf'
    expires = (stamp + timedelta(days=PDF_RETENTION_DAYS)).isoformat()
    db = get_db()
    with db.atomic():
        db.sql('DELETE FROM "Generated PDF" WHERE expires_at < ?', [stamp.isoformat()])
        db.sql('INSERT INTO "Generated PDF" (id,user_id,doctype,document_name,modified,filename,sha256,data,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
               [identifier, owner, doctype_slug, name, rendered.modified, filename, digest, data, stamp.isoformat(), expires])
    path = f'/documents/{quote(doctype_slug, safe="")}/{quote(name, safe="")}/pdf?artifact_id={identifier}'
    return {'doctype': doctype_slug, 'name': name, 'artifact_id': identifier,
            'filename': filename, 'mime_type': 'application/pdf', 'size_bytes': len(data),
            'sha256': digest, 'modified': rendered.modified, 'expires_at': expires,
            'kind': profile['kind'], 'pdf_url': '/api/v1' + path, 'download_url': '/api' + path,
            'validated': True, 'status': rendered.status, '_validation': {'warnings': rendered.warnings}}


def get_pdf_export(artifact_id, doctype_slug, name, user):
    rows = get_db().sql('SELECT * FROM "Generated PDF" WHERE id = ? AND user_id = ? AND doctype = ? AND document_name = ?',
                        [artifact_id, (user or {}).get('user_id') or (user or {}).get('name'), doctype_slug, name])
    if not rows:
        raise HTTPException(404, 'Generated PDF not found')
    row = rows[0]
    if row['expires_at'] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(410, 'Generated PDF expired; generate a new file')
    return bytes(row['data'])
