"""Company tax selection: scoped choices, validated writes and no retroactive changes."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api import services
from api.routers import masters
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from tests.test_default_tax import check_default_tax


def check_company_tax_settings():
    check_default_tax()
    db = get_db()
    company = 'Schweizer AG'
    template = db.get_value('Company', company, 'default_sales_tax_template')
    db.insert('Company', {'name': 'Other Co', 'company_name': 'Other Co'})
    db.insert('Tax Template', {'name': 'Other sales', 'company': 'Other Co', 'tax_type': 'Sales'})
    db.conn.commit()
    documents_before = db.get_all('Sales Invoice', fields=['*'])
    app = FastAPI()
    app.include_router(masters.router, prefix='/api')

    @app.exception_handler(ValidationError)
    async def validation_error(_request, error):
        return JSONResponse(status_code=422, content={'detail': str(error)})

    path = '/api/masters/company/Schweizer%20AG'
    with TestClient(app) as client:
        assert client.get(path + '/sales-tax-templates').status_code == 401
        app.dependency_overrides[masters._viewer.dependency] = lambda: {'role': 'viewer'}
        response = client.get(path + '/sales-tax-templates')
        assert response.status_code == 200, response.text
        choices = {row['name'] for row in response.json()}
        expected = {r['name'] for r in db.get_all('Tax Template',
            filters={'company': company, 'tax_type': 'Sales'}, fields=['name'])}
        assert choices == expected and template in choices and 'Other sales' not in choices
        assert client.get('/api/masters/company/Missing/sales-tax-templates').status_code == 404

        # Viewing choices grants no write permission.
        assert client.put(path, json={'default_sales_tax_template': None}).status_code == 401
        app.dependency_overrides[masters._manager.dependency] = lambda: {'role': 'manager'}
        purchase = db.get_all('Tax Template', filters={'company': company, 'tax_type': 'Purchase'},
                              fields=['name'])[0]['name']
        for invalid in ('Missing template', 'Other sales', purchase):
            response = client.put(path, json={'default_sales_tax_template': invalid})
            assert response.status_code == 422, response.text
            assert db.get_value('Company', company, 'default_sales_tax_template') == template

        # The form's empty option normalizes to NULL, and can be re-enabled.
        assert client.put(path, json={'default_sales_tax_template': ''}).status_code == 200
        assert db.get_value('Company', company, 'default_sales_tax_template') is None
        _, cls = services.get_document_class('quotation')
        assert services._default_tax_rows(cls, {'company': company}) is None
        assert client.put(path, json={'default_sales_tax_template': template}).status_code == 200
        assert services._default_tax_rows(cls, {'company': company})[0]['rate'] == 8.1
        assert db.get_all('Sales Invoice', fields=['*']) == documents_before
    print('PASS: company tax choices, permissions, invalid selections, clear/re-enable and unchanged invoices')


if __name__ == '__main__':
    check_company_tax_settings()
