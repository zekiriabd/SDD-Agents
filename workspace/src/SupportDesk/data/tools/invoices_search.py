# GÉNÉRÉ par gen_source_tools.py — NE PAS ÉDITER.
# Source `invoices` (file · store `support_data`) · confiance : trusted.
# Schéma figé : workspace/src/SupportDesk/data/schemas/invoices.schema.json
# Éditer ce fichier est [DATA_TOOL_HAND_EDITED] : corriger la DÉCLARATION de la
# source, puis `gen_source_tools.py --write`. Le code et la déclaration ne peuvent
# pas diverger sans que quelqu'un s'en aperçoive — c'est tout l'intérêt.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from SupportDesk.data.envelope import search_records
from SupportDesk.tools.spec import ToolContext, ToolSpec

SPEC = ToolSpec.from_contract("1-invoices-search")

SOURCE = "invoices"
PII_FIELDS: frozenset[str] = frozenset()
UNTRUSTED_FIELDS: frozenset[str] = frozenset()


class Input(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_id: str = Field(description="Filtre OBLIGATOIRE sur `customer_id` — égalité stricte, jamais une expression.")
    order_id: str | None = Field(default=None, description="Filtre optionnel sur `order_id` — égalité stricte, jamais une expression.")
    status: str | None = Field(default=None, description="Filtre optionnel sur `status` — égalité stricte, jamais une expression. Valeurs admises : issued, paid, partially_refunded, refunded, cancelled.")
    due_at_min: str | None = Field(default=None, description="Filtre de plage sur `due_at` : borne basse incluse. Même type et même unité que le champ.")
    due_at_max: str | None = Field(default=None, description="Filtre de plage sur `due_at` : borne haute incluse. Même type et même unité que le champ.")
    issued_at_min: str | None = Field(default=None, description="Filtre de plage sur `issued_at` : borne basse incluse. Même type et même unité que le champ.")
    issued_at_max: str | None = Field(default=None, description="Filtre de plage sur `issued_at` : borne haute incluse. Même type et même unité que le champ.")


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount_excl_tax: str = Field(description="Montant HT, EUR, chaîne à deux décimales.")
    amount_incl_tax: str = Field(description="Montant TTC, EUR, chaîne à deux décimales — le montant à citer par défaut.")
    currency: str = Field(description="Devise — toujours EUR.")
    customer_id: str = Field(description="Identifiant du client facturé (CUST-NNNN). Filtre d'identité ; non énumérable.")
    due_at: str = Field(description="Date d'échéance de règlement (date seule). Pertinente surtout pour un virement en attente.")
    invoice_id: str = Field(description="Numéro de facture (INV-AAAA-NNNN), à citer dans toute réponse de facturation.")
    issued_at: str = Field(description="Date d'émission, UTC ISO 8601.")
    order_id: str = Field(description="Numéro de la commande facturée.")
    pdf_available: bool = Field(description="true si le PDF de la facture est téléchargeable depuis l'espace client.")
    status: str = Field(description="issued (émise, non réglée), paid, partially_refunded, refunded, cancelled.")
    tax_amount: str = Field(description="Montant de TVA, EUR, chaîne à deux décimales.")
    tax_rate: str = Field(description="Taux de TVA en fraction (0.20 = 20 %).")


class Output(BaseModel):
    model_config = ConfigDict(frozen=True)

    records: list[Record] = Field(description="Enregistrements retenus, triés de façon déterministe.")
    truncated: bool = Field(description="Vrai si le plafond a été atteint : le total réel est SUPÉRIEUR — affiner les filtres avant de conclure.")
    as_of: str = Field(description="Instantané de la source, ISO 8601 UTC. La donnée a pu changer depuis.")
    stale: bool = Field(description="Vrai si l'instantané dépasse max_staleness_hours : le dire à l'utilisateur.")


async def invoices_search(params: Input, *, ctx: ToolContext) -> Output:
    """Recherche filtrée. Factures, une par commande confirmée (une commande `created` dont le paiement a"""
    return await search_records(
        source=SOURCE,
        filters=params.model_dump(exclude_none=True),
        record_model=Record,
        output_model=Output,
        ctx=ctx,
        pii_fields=PII_FIELDS,
        untrusted_fields=UNTRUSTED_FIELDS,
    )
