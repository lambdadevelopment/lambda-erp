"""Structural master fields cannot be reinterpreted after they are referenced."""

STRUCTURAL_FIELDS = {
    'account': ('company', 'root_type', 'report_type', 'account_type', 'account_currency', 'is_group', 'parent_account'),
    'warehouse': ('company', 'account', 'parent_warehouse', 'is_group'),
    'item': ('is_asset_tracked', 'is_stock_item', 'stock_uom'),
    'cost-center': ('company', 'parent_cost_center', 'is_group'),
    'company': ('default_currency',),
    # A batch is identified by its item and label once stock has moved on it.
    'batch': ('item_code', 'batch_no'),
    # A storage location is identified by its warehouse and label once stock has been in it.
    'storage-location': ('warehouse', 'location'),
}


def structural_changes(master_type, stored, incoming):
    def canonical(field, value):
        if field.startswith('is_'):
            return int(value or 0)
        return value or None
    return [field for field in STRUCTURAL_FIELDS.get(master_type, ())
            if field in incoming and canonical(field, incoming[field]) != canonical(field, stored.get(field))]


def structure_requirements(master_type):
    fields = STRUCTURAL_FIELDS.get(master_type, ())
    if not fields:
        return []
    return [f'Once referenced, structural fields ({", ".join(fields)}) cannot be changed through ordinary updates. Use a new master or a deliberate migration. Names/descriptions and unchanged values remain editable.']
