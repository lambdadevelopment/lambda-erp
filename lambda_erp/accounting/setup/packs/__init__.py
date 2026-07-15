"""Built-in localization packs.

Importing this subpackage registers every built-in pack as a side effect, so
that ``resolve_pack()`` works the moment ``lambda_erp.accounting.setup`` is
imported. Adding a jurisdiction = drop a module here and import it below.

    generic   the jurisdiction-neutral / international pack (and fallback)
    ch        Switzerland (Kontenrahmen KMU)
    de.skr03  Germany, DATEV SKR03 (process-ordered) — default for a bare `de`
    de.skr04  Germany, DATEV SKR04 (balance-sheet-ordered)
"""

from lambda_erp.accounting.setup.packs import generic  # noqa: F401
from lambda_erp.accounting.setup.packs import ch  # noqa: F401
from lambda_erp.accounting.setup.packs import de_skr03  # noqa: F401
from lambda_erp.accounting.setup.packs import de_skr04  # noqa: F401
